import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, String, false
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db._enum import pg_enum
from db.base import Base
from db.mixins import CreatedAtMixin, TZDateTime, UUIDPrimaryKeyMixin


class OrgPlan(enum.StrEnum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class UserRole(enum.StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class Organization(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[OrgPlan] = mapped_column(pg_enum(OrgPlan), default=OrgPlan.FREE, nullable=False)
    token_quota_monthly: Mapped[int] = mapped_column(default=0, nullable=False)
    token_used_current_period: Mapped[int] = mapped_column(default=0, nullable=False)

    users: Mapped[list["User"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class User(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "users"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        pg_enum(UserRole), default=UserRole.MEMBER, nullable=False
    )
    # Deployment-wide operator (not an organisation role): may create or
    # replace the GitHub App every organisation installs. Bootstrapped from
    # REVU_PLATFORM_ADMIN_EMAILS or `python -m api.cli platform-admin`.
    is_platform_admin: Mapped[bool] = mapped_column(
        default=False, server_default=false(), nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="users")


class GithubInstallation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "github_installations"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # GitHub IDs are 64-bit; BigInteger since Stage 10 (was Integer).
    installation_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    account_login: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str] = mapped_column(
        String(32), default="User", server_default="User", nullable=False
    )
    permissions: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    installed_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    # Lifecycle. Suspended: GitHub paused access; repositories go inactive
    # but keep their data. Uninstalled: the App was removed; the row stays so
    # its repositories (and their data) remain visible, grouped under it,
    # until the retention job purges them (or an admin deletes them sooner).
    suspended_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    uninstalled_at: Mapped[datetime | None] = mapped_column(TZDateTime)
