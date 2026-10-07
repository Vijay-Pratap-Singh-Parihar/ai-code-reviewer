"""The audit trail: one vocabulary, written by the API and the worker alike.

Audit entries answer "who did what to which resource, and when" for an
organisation's admins and for incident review. They are appended in the
same transaction as the change they describe, so a change is never recorded
without happening, or vice versa. They live in `audit_log`, which is under
row-level security like every tenant table, and they outlive the resources
they mention (there is no foreign key to the target), so deleting a
repository's data leaves the record that it was deleted.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from db.ledger import AuditLog


class AuditAction(enum.StrEnum):
    AUTH_LOGIN = "auth.login"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    PLATFORM_ADMIN_GRANTED = "platform_admin.granted"
    PLATFORM_ADMIN_REVOKED = "platform_admin.revoked"
    GITHUB_APP_CREATED = "github_app.created"
    INSTALLATION_LINKED = "installation.linked"
    INSTALLATION_SYNCED = "installation.synced"
    INSTALLATION_SUSPENDED = "installation.suspended"
    INSTALLATION_UNSUSPENDED = "installation.unsuspended"
    INSTALLATION_UNINSTALLED = "installation.uninstalled"
    REPOSITORY_CONNECTED = "repository.connected"
    REPOSITORY_DISCONNECTED = "repository.disconnected"
    REPOSITORY_SETTINGS_CHANGED = "repository.settings_changed"
    REVIEW_REQUESTED = "review.requested"
    INDEX_REQUESTED = "index.requested"
    DATA_DELETION_REQUESTED = "data.deletion_requested"
    DATA_PURGED = "data.purged"


def record_audit(
    session: AsyncSession | Session,
    *,
    org_id: uuid.UUID,
    action: AuditAction,
    target: str,
    actor_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append an entry to the session; it commits with the change itself.
    `actor_id` is None for actions taken by the system (webhooks, the
    retention job)."""
    session.add(
        AuditLog(
            org_id=org_id,
            actor_id=actor_id,
            action=str(action),
            target=target[:255],
            extra=metadata or {},
            # Python-side, per entry: Postgres now() is fixed for the whole
            # transaction, so several entries written together would tie and
            # lose their order.
            at=datetime.now(UTC),
        )
    )
