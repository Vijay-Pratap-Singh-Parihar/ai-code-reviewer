"""Provider credential encryption and endpoint resolution, without a database."""

import uuid

import pytest
from db.ai_endpoints import (
    NO_KEY_PLACEHOLDER,
    ProviderNotSupportedError,
    UnsafeEndpointError,
    check_endpoint_url,
    endpoint_for,
)
from db.credentials import (
    CredentialError,
    decrypt_credentials,
    encrypt_credentials,
    key_hint,
)
from db.provider import AIProvider, ProviderKind

SECRET = "deployment-secret"
ORG, PROVIDER = uuid.uuid4(), uuid.uuid4()


def test_credentials_round_trip_and_are_not_stored_in_plaintext() -> None:
    token = encrypt_credentials(
        SECRET, {"api_key": "sk-live-123456"}, org_id=ORG, provider_id=PROVIDER
    )

    assert token.startswith("v1:")
    assert "sk-live" not in token
    assert decrypt_credentials(SECRET, token, org_id=ORG, provider_id=PROVIDER) == {
        "api_key": "sk-live-123456"
    }


def test_each_encryption_uses_a_fresh_nonce() -> None:
    first = encrypt_credentials(SECRET, {"api_key": "k"}, org_id=ORG, provider_id=PROVIDER)
    second = encrypt_credentials(SECRET, {"api_key": "k"}, org_id=ORG, provider_id=PROVIDER)
    assert first != second


@pytest.mark.parametrize(
    ("org_id", "provider_id", "secret"),
    [
        (uuid.uuid4(), PROVIDER, SECRET),  # copied into another organisation's row
        (ORG, uuid.uuid4(), SECRET),  # copied onto another provider
        (ORG, PROVIDER, "a-different-deployment-secret"),
    ],
)
def test_credentials_only_decrypt_where_they_belong(
    org_id: uuid.UUID, provider_id: uuid.UUID, secret: str
) -> None:
    token = encrypt_credentials(SECRET, {"api_key": "k"}, org_id=ORG, provider_id=PROVIDER)
    with pytest.raises(CredentialError):
        decrypt_credentials(secret, token, org_id=org_id, provider_id=provider_id)


def test_tampered_credentials_are_rejected() -> None:
    token = encrypt_credentials(SECRET, {"api_key": "k"}, org_id=ORG, provider_id=PROVIDER)
    tampered = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
    with pytest.raises(CredentialError):
        decrypt_credentials(SECRET, tampered, org_id=ORG, provider_id=PROVIDER)


@pytest.mark.parametrize(
    ("api_key", "hint"),
    [
        ("sk-ant-api03-abcdefghijkl1234", "sk-…1234"),
        ("gsk_abcdefghijklmnop9876", "gsk…9876"),
        ("short", "…rt"),
        (None, None),
    ],
)
def test_key_hint_shows_too_little_to_use_the_key(api_key: str | None, hint: str | None) -> None:
    assert key_hint(api_key) == hint


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud instance metadata
        "http://[fe80::1]/v1",
        "ftp://example.com/v1",
        "https://user:pass@example.com/v1",
        "not a url",
    ],
)
def test_unsafe_endpoints_are_always_refused(url: str) -> None:
    with pytest.raises(UnsafeEndpointError):
        check_endpoint_url(url, allow_private=True)


def test_private_endpoints_need_the_deployment_to_allow_them() -> None:
    check_endpoint_url("http://127.0.0.1:11434/v1", allow_private=True)
    with pytest.raises(UnsafeEndpointError, match="private"):
        check_endpoint_url("http://127.0.0.1:11434/v1", allow_private=False)
    with pytest.raises(UnsafeEndpointError, match="private"):
        check_endpoint_url("http://10.0.0.5/v1", allow_private=False)


def _provider(kind: ProviderKind, *, base_url: str | None = None, **creds: object) -> AIProvider:
    provider = AIProvider(
        id=PROVIDER, org_id=ORG, name="p", kind=kind, base_url=base_url, settings={}
    )
    provider.encrypted_credentials = encrypt_credentials(
        SECRET, dict(creds), org_id=ORG, provider_id=PROVIDER
    )
    return provider


async def test_hosted_providers_use_their_litellm_prefix_and_the_stored_key() -> None:
    endpoint = await endpoint_for(
        _provider(ProviderKind.GROQ, api_key="gsk_x"),
        "openai/gpt-oss-120b",
        secret=SECRET,
        allow_private=False,
    )
    assert endpoint.model == "groq/openai/gpt-oss-120b"
    assert endpoint.api_key == "gsk_x"
    assert endpoint.api_base is None  # LiteLLM's own Groq URL
    assert "gsk_x" not in repr(endpoint)


async def test_a_keyless_local_server_gets_a_placeholder_key_not_the_environment() -> None:
    endpoint = await endpoint_for(
        _provider(ProviderKind.OPENAI_COMPATIBLE, base_url="http://127.0.0.1:11434/v1"),
        "qwen3.5:9b",
        secret=SECRET,
        allow_private=True,
    )
    assert endpoint.model == "openai/qwen3.5:9b"
    assert endpoint.api_base == "http://127.0.0.1:11434/v1"
    assert endpoint.api_key == NO_KEY_PLACEHOLDER


async def test_endpoint_urls_are_checked_again_at_call_time() -> None:
    with pytest.raises(UnsafeEndpointError):
        await endpoint_for(
            _provider(ProviderKind.OPENAI_COMPATIBLE, base_url="http://127.0.0.1:11434/v1"),
            "m",
            secret=SECRET,
            allow_private=False,
        )


async def test_reserved_provider_kinds_are_not_usable_yet() -> None:
    with pytest.raises(ProviderNotSupportedError):
        await endpoint_for(
            _provider(ProviderKind.BEDROCK, api_key="k"), "m", secret=SECRET, allow_private=False
        )
