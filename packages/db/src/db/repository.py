import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    false,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base
from db.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin, inherited_org_id


class Repository(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A repository as one organisation sees it.

    Names and GitHub ids are unique *per organisation*: two organisations
    connecting the same GitHub repository get two fully separate rows (and
    clones, indexes and reviews), never a shared one.
    """

    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("org_id", "full_name", name="uq_repositories_org_full_name"),
        UniqueConstraint("org_id", "github_repo_id", name="uq_repositories_org_github_repo_id"),
        # Target of the composite (repo_id, org_id) foreign keys below.
        UniqueConstraint("id", "org_id", name="uq_repositories_id_org"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    installation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("github_installations.id", ondelete="SET NULL")
    )
    # Set for repos synced from a GitHub App installation; NULL for repos
    # created by the manual diff-paste path. Stable across repo renames.
    github_repo_id: Mapped[int | None] = mapped_column(BigInteger)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), default="main", nullable=False)
    languages: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Reviewing on every PR open/push costs real LLM tokens, so it is opt-in
    # per repository: off by default, toggled from the Repositories screen.
    auto_review_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    config_json: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)

    tracked_branches: Mapped[list["TrackedBranch"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )


class TrackedBranch(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "tracked_branches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["repo_id", "org_id"],
            ["repositories.id", "repositories.org_id"],
            ondelete="CASCADE",
            name="fk_tracked_branches_repo_org",
        ),
    )

    org_id: Mapped[uuid.UUID] = inherited_org_id()
    repo_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    branch_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_protected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_index: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    repository: Mapped[Repository] = relationship(back_populates="tracked_branches")
