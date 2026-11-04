"""Error reporting to Sentry (or anything that speaks its protocol, such as
a self-hosted GlitchTip), switched on only by setting `SENTRY_DSN`.

What is sent is the exception and its stack, tagged with the service and
the request id the logs carry, so an error in Sentry leads straight to its
log lines. No personal data: no cookies, headers, request bodies or
user emails (`send_default_pii=False`), and provider keys and tokens are
masked in the exception text and breadcrumbs as they are in the logs.
"""

from __future__ import annotations

import logging
from typing import Any

from observability.context import current_fields
from observability.logs import redact

logger = logging.getLogger(__name__)

# Context fields worth a Sentry tag (searchable); ids, not names or emails.
_TAGGED = ("request_id", "job", "job_id", "attempt", "run_id", "org_id", "repo_id")


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def before_send(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
    """Tag the event with the current request/job context and mask any
    credential in its text."""
    tags = event.setdefault("tags", {})
    for key, value in current_fields().items():
        if key in _TAGGED and value is not None:
            tags.setdefault(key, str(value))
    for exc in (event.get("exception") or {}).get("values") or []:
        if isinstance(exc.get("value"), str):
            exc["value"] = redact(exc["value"])
    if "logentry" in event:
        event["logentry"] = _scrub(event["logentry"])
    if "message" in event:
        event["message"] = _scrub(event["message"])
    # Local variables in stack frames can hold a decrypted key.
    for exc in (event.get("exception") or {}).get("values") or []:
        for frame in (exc.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)
    return event


def before_breadcrumb(crumb: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
    return _scrub(crumb)  # type: ignore[no-any-return]


def init_error_reporting(
    *,
    dsn: str | None,
    service: str,
    environment: str,
    release: str | None = None,
    traces_sample_rate: float = 0.0,
    integrations: list[Any] | None = None,
) -> bool:
    """Start reporting errors when a DSN is configured. Returns whether it
    did. Without one this does nothing and imports nothing."""
    if not dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        server_name=service,
        send_default_pii=False,
        include_local_variables=False,
        traces_sample_rate=traces_sample_rate,
        before_send=before_send,  # type: ignore[arg-type]
        before_breadcrumb=before_breadcrumb,
        integrations=integrations or [],
    )
    sentry_sdk.set_tag("service", service)
    logger.info("error reporting to Sentry is on (environment %s)", environment)
    return True
