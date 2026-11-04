"""Benchmark systems that name a provider from the AI Providers screen.

The DB-backed tests run against the shared test database (built by the real
migrations, so row-level security applies) and skip without Postgres."""

import uuid

import pytest
from db.credentials import encrypt_credentials
from db.organization import Organization
from db.provider import AIProvider, ProviderKind
from db.testing import (
    TEST_APP_DATABASE_URL,
    TEST_DATABASE_URL,
    DatabaseUnavailable,
    async_url,
    prepare_test_database,
)
from pydantic import ValidationError
from revu_bench.providers import ProviderLookupError, resolve_ui_providers
from revu_bench.runner import SystemConfig, is_hosted
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

SECRET = "bench-test-secret"


def _add_org_with_groq(name: str, api_key: str) -> None:
    """Written as the owner role, which bypasses row-level security."""
    engine = create_engine(TEST_DATABASE_URL)
    try:
        with Session(engine) as session:
            org = Organization(id=uuid.uuid4(), name=name)
            session.add(org)
            session.flush()
            provider_id = uuid.uuid4()
            session.add(
                AIProvider(
                    id=provider_id,
                    org_id=org.id,
                    name="Groq",
                    kind=ProviderKind.GROQ,
                    encrypted_credentials=encrypt_credentials(
                        SECRET, {"api_key": api_key}, org_id=org.id, provider_id=provider_id
                    ),
                    settings={},
                )
            )
            session.commit()
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def orgs() -> tuple[str, str]:
    try:
        prepare_test_database()
    except DatabaseUnavailable:
        pytest.skip("no Postgres for the test database")
    mine, theirs = f"Bench org {uuid.uuid4().hex[:8]}", f"Other org {uuid.uuid4().hex[:8]}"
    _add_org_with_groq(mine, "gsk_mine")
    _add_org_with_groq(theirs, "gsk_theirs")
    return mine, theirs


def _system(org: str, provider: str = "Groq") -> SystemConfig:
    return SystemConfig(
        name="groq-120b", org=org, provider=provider, model="openai/gpt-oss-120b", reasoning="low"
    )


async def test_a_ui_provider_resolves_to_its_own_decrypted_endpoint(orgs: tuple[str, str]) -> None:
    mine, theirs = orgs
    systems = [_system(mine), _system(theirs).model_copy(update={"name": "other"})]

    endpoints = await resolve_ui_providers(
        systems, database_url=async_url(TEST_APP_DATABASE_URL), secret=SECRET
    )

    assert endpoints["groq-120b"].model == "groq/openai/gpt-oss-120b"
    assert endpoints["groq-120b"].api_key == "gsk_mine"
    assert endpoints["groq-120b"].reasoning == "low"
    assert endpoints["other"].api_key == "gsk_theirs"  # each org's own key, never mixed

    systems[0].use_endpoint(endpoints["groq-120b"])
    assert systems[0].endpoint() is endpoints["groq-120b"]
    assert is_hosted(systems[0])  # a hosted API: the run needs a budget


async def test_an_unknown_organization_or_provider_is_an_error(orgs: tuple[str, str]) -> None:
    mine, _ = orgs
    url = async_url(TEST_APP_DATABASE_URL)

    with pytest.raises(ProviderLookupError, match="no organizations named"):
        await resolve_ui_providers([_system("No such org")], database_url=url, secret=SECRET)
    with pytest.raises(ProviderLookupError, match="no AI provider named 'OpenAI'"):
        await resolve_ui_providers([_system(mine, "OpenAI")], database_url=url, secret=SECRET)


async def test_systems_without_a_ui_provider_need_no_database() -> None:
    local = SystemConfig(
        name="local", model="openai/qwen3.5:4b", api_base="http://127.0.0.1:11434/v1"
    )

    assert await resolve_ui_providers([local], database_url=None) == {}


def test_a_ui_provider_system_names_both_org_and_provider_and_nothing_else() -> None:
    with pytest.raises(ValidationError, match="both `org` and `provider`"):
        SystemConfig(name="x", model="m", provider="Groq")
    with pytest.raises(ValidationError, match="brings its own endpoint"):
        SystemConfig(name="x", model="m", org="Acme", provider="Groq", api_key_env="GROQ_API_KEY")
    with pytest.raises(RuntimeError, match="hasn't been resolved"):
        SystemConfig(name="x", model="m", org="Acme", provider="Groq").endpoint()
