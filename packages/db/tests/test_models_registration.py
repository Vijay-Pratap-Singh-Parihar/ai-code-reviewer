import db
from db.base import Base


def test_importing_db_registers_every_model_on_base_metadata() -> None:
    expected_tables = {
        "organizations",
        "users",
        "github_installations",
        "repositories",
        "tracked_branches",
        "branch_index",
        "index_update_log",
        "ai_providers",
        "model_routes",
        "pull_requests",
        "analysis_runs",
        "findings",
        "context_bundles",
        "token_usage_ledger",
        "audit_log",
        "refresh_tokens",
    }
    assert expected_tables <= set(Base.metadata.tables)


def test_all_exports_are_importable() -> None:
    for name in db.__all__:
        assert hasattr(db, name)
