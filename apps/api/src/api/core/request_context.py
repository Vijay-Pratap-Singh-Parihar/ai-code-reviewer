"""Every request gets an id, returned in `X-Request-ID`, carried by every log
line written while handling it and by every job it queues
(`api.core.queue.enqueue`), and ends with one access log line and a count
and duration in the request metrics (`observability.metrics`).

A pure ASGI middleware rather than Starlette's `BaseHTTPMiddleware`, so
streamed responses (the live provider test) pass straight through.
"""

from __future__ import annotations

import logging
import time

from observability import REQUEST_ID_HEADER, bound, clean_request_id, metrics, new_request_id
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("api.access")

# Load balancers and the compose healthcheck poll these every few seconds.
_QUIET_PATHS = ("/health", "/health/live", "/health/ready", "/metrics")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER)
        request_id = clean_request_id(incoming) or new_request_id()
        status = 500
        start = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            await send(message)

        with bound(request_id=request_id):
            try:
                await self.app(scope, receive, send_with_id)
            except Exception:
                # Logged here, inside the request's context, so the traceback
                # carries its request id; Starlette still answers 500.
                logger.exception("unhandled error in %s %s", scope["method"], scope["path"])
                raise
            finally:
                elapsed = time.perf_counter() - start
                path = scope["path"]
                method = scope["method"]
                route = _route_template(scope)
                metrics.HTTP_REQUESTS.labels(method, route, str(status)).inc()
                metrics.HTTP_DURATION.labels(method, route).observe(elapsed)
                level = logging.DEBUG if path in _QUIET_PATHS else logging.INFO
                logger.log(
                    level,
                    "%s %s %d",
                    method,
                    path,
                    status,
                    extra={"status": status, "duration_ms": round(elapsed * 1000)},
                )


def _route_template(scope: Scope) -> str:
    """The matched route's template (`/analysis/{run_id}`), never the raw
    path, so ids don't each become a metric series. Unmatched paths (404s,
    scanners probing) share one label."""
    route = scope.get("route")
    template = getattr(route, "path", None)
    return template if isinstance(template, str) else "unmatched"
