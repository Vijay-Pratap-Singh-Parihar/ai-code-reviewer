"""Proves `revu.index.store.to_branch_index_fields` (packages/engine) is
shaped correctly for `db.BranchIndex` (packages/db) — the one integration
point Stage 4 needs to guarantee even though the two packages can't import
each other directly (see `store.py`'s module docstring for why: `db`
depends on `revu`, so the reverse import would be circular). Stage 5 is
where a `BranchIndex` row's full lifecycle (staleness, force-push rebuild
detection, etc.) actually gets wired up in `apps/worker`; this test only
proves the hand-off dict this stage produces is valid input for that model,
against a real Postgres round-trip rather than a mock.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from db.base import Base
from db.branch_index import BranchIndex, BranchIndexStatus
from db.organization import Organization
from db.repository import Repository
from revu.index import store
from revu.index.graph import build_index_at_path
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://revu:revu_dev_password@localhost:5434/revu_test",
)


@pytest.fixture(scope="module")
def db_engine() -> Iterator[Engine]:
    engine = create_engine(TEST_DATABASE_URL)
    try:
        with engine.connect():
            pass
    except OperationalError:
        pytest.skip(f"no Postgres reachable at {TEST_DATABASE_URL}; skipping DB-backed tests")

    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    connection = db_engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection)()
    yield session
    session.close()
    if transaction.is_active:
        transaction.rollback()
    connection.close()


def test_indexer_output_round_trips_through_a_real_branch_index_row(
    db_session: Session, tmp_path: Path
) -> None:
    org = Organization(name="Acme Inc")
    db_session.add(org)
    db_session.flush()

    repo_row = Repository(org_id=org.id, full_name="acme/widgets")
    db_session.add(repo_row)
    db_session.flush()

    (tmp_path / "a.py").write_text("def f():\n    pass\n")
    result = build_index_at_path(tmp_path)

    graph_path = store.save_graph(
        result.graph,
        repo_identifier="acme/widgets",
        branch_name="main",
        head_sha="a" * 40,
        storage_dir=tmp_path / ".graphs",
    )

    fields = store.to_branch_index_fields(
        node_count=result.node_count,
        edge_count=result.edge_count,
        graph_ref=str(graph_path),
        unresolved=[u.model_dump() for u in result.unresolved],
        build_duration_ms=result.duration_ms,
    )

    row = BranchIndex(
        repo_id=repo_row.id,
        branch_name="main",
        head_sha="a" * 40,
        status=BranchIndexStatus.READY,
        **fields,
    )
    db_session.add(row)
    db_session.commit()

    fetched = db_session.execute(
        select(BranchIndex).where(BranchIndex.id == row.id)
    ).scalar_one()
    assert fetched.node_count == result.node_count
    assert fetched.edge_count == result.edge_count
    assert fetched.graph_ref == str(graph_path)
    assert fetched.status == BranchIndexStatus.READY
    assert isinstance(fetched.unresolved_symbols, list)

    # And the blob it points at is really loadable back into a graph.
    reloaded_graph = store.load_graph(graph_path)
    assert reloaded_graph.num_nodes() == result.node_count
