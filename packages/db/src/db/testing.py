"""Test-suite database setup, shared by every package's tests.

Tests build the schema by running the real Alembic migrations, not
`Base.metadata.create_all`: the row-level security policies, the
`revu_app` role and the `org_id` inheritance trigger only exist in the
migrations, and tests that skip them would prove nothing about isolation.

The schema is rebuilt once per test process and never dropped afterwards,
so every package's tests in one `pytest` run share it.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

# Owner (migration) role: bypasses RLS, used to build the schema and by the
# model-level tests that are about columns and constraints, not tenancy.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://revu:revu_dev_password@localhost:5434/revu_test",
)
# Application role: what the API and worker connect as. RLS applies.
TEST_APP_DATABASE_URL = os.environ.get(
    "TEST_APP_DATABASE_URL",
    "postgresql+psycopg://revu_app:revu_app_dev_password@localhost:5434/revu_test",
)

_API_DIR = Path(__file__).resolve().parents[4] / "apps" / "api"
_prepared = False


class DatabaseUnavailable(RuntimeError):
    pass


def async_url(url: str) -> str:
    return url.replace("+psycopg", "+asyncpg")


def prepare_test_database() -> None:
    """Reset the test database and migrate it to head, once per process.

    Raises `DatabaseUnavailable` when Postgres can't be reached, so
    callers can skip their DB-backed tests instead of failing.
    """
    global _prepared
    if _prepared:
        return

    from alembic import command
    from alembic.config import Config

    engine = create_engine(TEST_DATABASE_URL)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    except OperationalError as exc:
        raise DatabaseUnavailable(str(exc)) from exc
    finally:
        engine.dispose()

    config = Config(str(_API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_API_DIR / "alembic"))
    config.attributes["url"] = TEST_DATABASE_URL
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    _prepared = True
