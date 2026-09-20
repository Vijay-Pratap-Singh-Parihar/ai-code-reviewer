import uuid

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base
from db.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin


class Repository(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "repositories"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    installation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("github_installations.id", ondelete="SET NULL")
    )
    full_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), default="main", nullable=False)
    languages: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config_json: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)

    tracked_branches: Mapped[list["TrackedBranch"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )


class TrackedBranch(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "tracked_branches"

    repo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    branch_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_protected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_index: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    repository: Mapped[Repository] = relationship(back_populates="tracked_branches")
