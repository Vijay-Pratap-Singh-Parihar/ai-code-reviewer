from collections.abc import AsyncIterator
from pathlib import Path

import db as _db_models  # noqa: F401  (registers all models on Base.metadata)
import pytest
import pytest_asyncio
from db.testing import (
    TEST_APP_DATABASE_URL,
    TEST_DATABASE_URL,
    DatabaseUnavailable,
    async_url,
    prepare_test_database,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from worker.storage import TenantStorage, signing_key_from_secret


def _skip_if_unreachable() -> None:
    try:
        prepare_test_database()
    except DatabaseUnavailable:
        pytest.skip(f"no Postgres reachable at {TEST_DATABASE_URL}; skipping DB-backed tests")


@pytest_asyncio.fixture
async def worker_db_session() -> AsyncIterator[AsyncSession]:
    """A real async session against the test database, connected as the
    application role (row-level security applies, as in production), wrapped
    in a transaction that's rolled back afterwards — same pattern as the
    API's `api_client` fixture, so the worker job under test runs its own
    `session.commit()` calls without leaking data between tests.
    """
    _skip_if_unreachable()

    engine = create_async_engine(async_url(TEST_APP_DATABASE_URL))
    connection = await engine.connect()
    transaction = await connection.begin()
    session_factory = async_sessionmaker(
        bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    session = session_factory()

    yield session

    await session.close()
    await transaction.rollback()
    await connection.close()
    await engine.dispose()


class _NoCloseSessionContext:
    """`async with` support that hands back the shared session without
    closing it on exit — `analyze_pr` closes whatever its `async with
    session_factory()` block gives it, and a real close would make the
    session unusable for the test's own post-job assertions.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _SingleSessionFactory:
    """Mimics `async_sessionmaker()` but always hands back the same
    (never-closed) session, so the job's `async with session_factory()`
    participates in the test's own transaction instead of opening a second
    connection.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> _NoCloseSessionContext:
        return _NoCloseSessionContext(self._session)


@pytest.fixture
def tenant_storage(tmp_path: Path) -> TenantStorage:
    return TenantStorage(
        repo_cache_root=tmp_path / "repos",
        index_root=tmp_path / "index",
        signing_key=signing_key_from_secret("test-secret"),
    )


@pytest.fixture
def worker_ctx(worker_db_session: AsyncSession, tenant_storage: TenantStorage) -> dict[str, object]:
    return {
        "db_session_factory": _SingleSessionFactory(worker_db_session),
        "storage": tenant_storage,
    }
