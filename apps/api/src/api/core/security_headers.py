"""Security headers on every API response.

The API only ever answers JSON (and a newline-delimited JSON stream), so
its Content-Security-Policy allows nothing at all: if a response were ever
rendered as a page, it could run no script, load nothing, and not be
framed. The interactive docs (`/docs`, `/redoc`) load Swagger/ReDoc from a
CDN and get their own, looser policy; production turns them off
(`api.main`).

HSTS is sent in production only: on plain-HTTP development it would make
browsers refuse http://localhost for a year.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_COMMON = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://fastapi.tiangolo.com; "
    "worker-src 'self' blob:; frame-ancestors 'none'"
)
HSTS = "max-age=31536000; includeSubDomains"
_DOCS_PATHS = ("/docs", "/redoc", "/docs/oauth2-redirect", "/openapi.json")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        csp = DOCS_CSP if scope["path"] in _DOCS_PATHS else API_CSP

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _COMMON.items():
                    headers.setdefault(name, value)
                headers.setdefault("Content-Security-Policy", csp)
                if self.hsts:
                    headers.setdefault("Strict-Transport-Security", HSTS)
            await send(message)

        await self.app(scope, receive, send_with_headers)
