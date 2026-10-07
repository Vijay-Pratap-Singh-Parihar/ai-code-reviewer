from collections.abc import AsyncIterator, Iterator

import db as _db_models  # noqa: F401  (registers all models on Base.metadata)
import pytest
import pytest_asyncio
from api.core.queue import get_redis_pool
from api.db.session import get_db
from api.main import app
from db.testing import (
    TEST_APP_DATABASE_URL,
    TEST_DATABASE_URL,
    DatabaseUnavailable,
    async_url,
    prepare_test_database,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker


class FakeRedisPool:
    """Records `enqueue_job` calls instead of touching real Redis.

    API-level tests only need to assert that the right job was enqueued with
    the right arguments; whether the worker actually processes it is a
    separate concern, covered by testing `worker.jobs.analyze.analyze_pr`
    directly against a fake DB session.
    """

    def __init__(self) -> None:
        self.enqueued: list[tuple[str, tuple[object, ...]]] = []

    async def enqueue_job(self, function: str, *args: object, **kwargs: object) -> None:
        self.enqueued.append((function, args))


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    """A sync engine, as the schema owner, against a test database built by
    the real migrations (`db.testing`). The owner bypasses row-level
    security; it backs the model-level tests only. `api_client` connects as
    the application role, where RLS applies.

    Skips the whole session's DB-backed tests (rather than failing) when no
    Postgres is reachable, so `uv run pytest` still passes on a machine that
    hasn't started `docker compose up postgres` yet.
    """
    try:
        prepare_test_database()
    except DatabaseUnavailable:
        pytest.skip(f"no Postgres reachable at {TEST_DATABASE_URL}; skipping DB-backed tests")

    engine = create_engine(TEST_DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    """One session per test, wrapped in a transaction that's always rolled
    back so tests never leak state into each other.
    """
    connection = db_engine.connect()
    transaction = connection.begin()
    session_factory = sessionmaker(bind=connection)
    session = session_factory()

    yield session

    session.close()
    # A test that triggers an IntegrityError (expected, via pytest.raises)
    # leaves the DBAPI transaction already rolled back by the failed flush.
    if transaction.is_active:
        transaction.rollback()
    connection.close()


@pytest_asyncio.fixture
async def api_client(db_engine: Engine) -> AsyncIterator[AsyncClient]:
    """An httpx client driving the real FastAPI app end to end (real routing,
    real Pydantic validation, real cookie handling) against the test
    database, connected as `revu_app` exactly like production, so every
    request runs under row-level security. Everything the app does inside
    one HTTP call — including its own `session.commit()` calls — runs inside
    one outer transaction that's rolled back afterwards, via SQLAlchemy's
    `create_savepoint` join mode.

    Depending on `db_engine` (not just its side effect of creating tables)
    means the "no Postgres reachable" skip propagates here too.
    """
    engine = create_async_engine(async_url(TEST_APP_DATABASE_URL))
    connection = await engine.connect()
    outer_transaction = await connection.begin()
    session_factory = async_sessionmaker(
        bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )

    async def override_get_db() -> AsyncIterator[None]:
        async with session_factory() as session:
            yield session

    fake_redis_pool = FakeRedisPool()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis_pool] = lambda: fake_redis_pool

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.fake_redis_pool = fake_redis_pool  # type: ignore[attr-defined]
        yield client

    app.dependency_overrides.clear()
    await outer_transaction.rollback()
    await connection.close()
    await engine.dispose()
