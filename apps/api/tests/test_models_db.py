from datetime import UTC, datetime

import pytest
from db.organization import Organization, OrgPlan, User, UserRole
from db.provider import AIProvider, ModelRoute, ModelTier, ProviderKind
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _make_org(session: Session, **overrides: object) -> Organization:
    defaults: dict[str, object] = {"name": "Acme Inc", "token_quota_monthly": 1_000_000}
    defaults.update(overrides)
    org = Organization(**defaults)  # type: ignore[arg-type]
    session.add(org)
    session.flush()
    return org


def test_organization_defaults_to_free_plan(db_session: Session) -> None:
    org = _make_org(db_session)
    assert org.plan == OrgPlan.FREE
    assert org.token_used_current_period == 0


def test_enum_columns_store_lowercase_value_not_member_name(db_session: Session) -> None:
    """Regression guard for the `pg_enum` helper: without `values_callable`,
    SQLAlchemy stores the Python enum *name* ("ADMIN"), not the StrEnum
    *value* ("admin"), which would silently diverge from what the API/JSON
    layer serializes.
    """
    org = _make_org(db_session)
    user = User(org_id=org.id, email="a@example.com", password_hash="x", role=UserRole.ADMIN)
    db_session.add(user)
    db_session.flush()

    raw_role = db_session.execute(
        text("SELECT role::text FROM users WHERE id = :id"), {"id": user.id}
    ).scalar_one()
    assert raw_role == "admin"


def test_deleting_organization_cascades_to_users(db_session: Session) -> None:
    org = _make_org(db_session)
    user = User(org_id=org.id, email="b@example.com", password_hash="x", role=UserRole.MEMBER)
    db_session.add(user)
    db_session.flush()
    user_id = user.id

    db_session.delete(org)
    db_session.flush()

    assert db_session.get(User, user_id) is None


def test_repository_config_json_roundtrips_as_dict(db_session: Session) -> None:
    from db.repository import Repository

    org = _make_org(db_session)
    repo = Repository(
        org_id=org.id,
        full_name="acme/widgets",
        languages=["python"],
        config_json={"ignore_globs": ["*.lock"], "comment_cap": 10},
    )
    db_session.add(repo)
    db_session.flush()
    db_session.expire(repo)

    fetched = db_session.get(Repository, repo.id)
    assert fetched is not None
    assert fetched.config_json == {"ignore_globs": ["*.lock"], "comment_cap": 10}
    assert fetched.languages == ["python"]


def test_model_route_unique_per_org_and_tier(db_session: Session) -> None:
    org = _make_org(db_session)
    provider = AIProvider(
        org_id=org.id,
        kind=ProviderKind.ANTHROPIC,
        encrypted_credentials="ciphertext",
        verified_at=datetime.now(UTC),
    )
    db_session.add(provider)
    db_session.flush()

    db_session.add(
        ModelRoute(org_id=org.id, tier=ModelTier.REVIEW, provider_id=provider.id,
                    model_name="claude-sonnet-5")
    )
    db_session.flush()

    db_session.add(
        ModelRoute(org_id=org.id, tier=ModelTier.REVIEW, provider_id=provider.id,
                    model_name="claude-haiku-4-5")
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_user_email_is_unique(db_session: Session) -> None:
    org = _make_org(db_session)
    db_session.add(
        User(org_id=org.id, email="dupe@example.com", password_hash="x", role=UserRole.MEMBER)
    )
    db_session.flush()

    db_session.add(
        User(org_id=org.id, email="dupe@example.com", password_hash="y", role=UserRole.MEMBER)
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_query_users_by_org(db_session: Session) -> None:
    org = _make_org(db_session)
    db_session.add_all(
        [
            User(org_id=org.id, email="c@example.com", password_hash="x", role=UserRole.OWNER),
            User(org_id=org.id, email="d@example.com", password_hash="x", role=UserRole.MEMBER),
        ]
    )
    db_session.flush()

    users = db_session.scalars(select(User).where(User.org_id == org.id)).all()
    assert {u.email for u in users} == {"c@example.com", "d@example.com"}
