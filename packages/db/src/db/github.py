from datetime import datetime

from sqlalchemy import String, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.mixins import TZDateTime, UUIDPrimaryKeyMixin


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
