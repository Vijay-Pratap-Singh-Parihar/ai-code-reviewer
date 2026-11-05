"""Security headers on API responses."""

from api.core.security_headers import API_CSP, HSTS, SecurityHeadersMiddleware
from api.main import app
from fastapi import FastAPI
from fastapi.testclient import TestClient

client = TestClient(app)


def test_every_response_carries_the_security_headers() -> None:
    for response in (client.get("/health"), client.get("/no-such-route")):
        headers = response.headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"] == "no-referrer"
        assert headers["Content-Security-Policy"] == API_CSP
        assert "camera=()" in headers["Permissions-Policy"]


def test_no_hsts_outside_production() -> None:
    assert "Strict-Transport-Security" not in client.get("/health").headers


def test_hsts_when_enabled_and_the_docs_get_their_own_policy() -> None:
    small = FastAPI()

    @small.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "yes"}

    small.add_middleware(SecurityHeadersMiddleware, hsts=True)
    local = TestClient(small)

    assert local.get("/ping").headers["Strict-Transport-Security"] == HSTS
    docs_csp = local.get("/docs").headers["Content-Security-Policy"]
    assert "cdn.jsdelivr.net" in docs_csp and docs_csp != API_CSP


def test_cors_preflight_still_works_with_the_headers() -> None:
    response = client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
