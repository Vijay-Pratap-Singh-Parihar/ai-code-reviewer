import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.mixins import TZDateTime, UUIDPrimaryKeyMixin


class GitHubAppCredentials(Base):
    """The one GitHub App this revu deployment uses, when it was created
    through the in-app manifest flow rather than configured via `GITHUB_*`
    environment variables (which take precedence when set).

    A singleton: `id` is pinned to 1. Secrets are stored encrypted with
    `ghapp.SecretBox` (keyed from `CREDENTIAL_ENCRYPTION_KEY`), never in
    plaintext; the identifiers alongside them are not secret.
    """

    __tablename__ = "github_app_credentials"
    __table_args__ = (CheckConstraint("id = 1", name="ck_github_app_credentials_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    app_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_login: Mapped[str] = mapped_column(String(255), nullable=False)
    html_url: Mapped[str] = mapped_column(String(500), nullable=False)
    client_id: Mapped[str] = mapped_column(String(100), nullable=False)
    client_secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    webhook_secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    # The smee.io channel GitHub delivers webhooks to in local development
    # (relayed to the API by the `webhook-relay` service); NULL when GitHub
    # posts straight to a public URL.
    webhook_proxy_url: Mapped[str | None] = mapped_column(String(500))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, server_default=func.now(), nullable=False
    )


class WebhookDelivery(UUIDPrimaryKeyMixin, Base):
    """One processed GitHub webhook delivery, keyed by `X-GitHub-Delivery`.

    GitHub retries (and lets users redeliver) webhooks, so the receiver
    records each delivery ID and skips any it has already seen. Otherwise a
    retried `pull_request.opened` would enqueue a second paid review.
    """

    __tablename__ = "webhook_deliveries"

    delivery_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str | None] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(
        TZDateTime, server_default=func.now(), nullable=False
    )
