"""Rate limits on sign-in, sign-up and model-spending endpoints."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from api.core import rate_limit
from api.core.config import get_settings
from api.core.queue import get_redis_pool
from api.main import app
from api.testing import configure_review_model
from httpx import AsyncClient


class _CountingRedis:
    """Enough of Redis for the limiter (INCR/EXPIRE) and the job queue."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.enqueued: list[tuple[str, tuple[object, ...]]] = []
        self.down = False

    async def incr(self, key: str) -> int:
        if self.down:
            raise ConnectionError("redis down")
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def enqueue_job(self, function: str, *args: object, **kwargs: object) -> None:
        self.enqueued.append((function, args))


@pytest_asyncio.fixture
async def limited(
    api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[_CountingRedis]:
    redis = _CountingRedis()
    monkeypatch.setattr(get_settings(), "revu_rate_limits", True)
    app.dependency_overrides[get_redis_pool] = lambda: redis
    yield redis


def _signup(email: str) -> dict[str, str]:
    return {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "org_name": "Acme",
        "email": email,
        "password": "correct-horse-battery",
    }


async def test_signups_from_one_address_are_limited(
    api_client: AsyncClient, limited: _CountingRedis
) -> None:
    for i in range(rate_limit.SIGNUP.limit):
        response = await api_client.post("/auth/signup", json=_signup(f"s{i}@example.com"))
        assert response.status_code == 201, response.text

    blocked = await api_client.post("/auth/signup", json=_signup("one-more@example.com"))

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    assert "5 per 60 minutes" in blocked.json()["detail"]


async def test_password_guesses_against_one_account_are_limited(
    api_client: AsyncClient, limited: _CountingRedis
) -> None:
    assert rate_limit.LOGIN_EMAIL.limit + 2 < rate_limit.LOGIN_ADDRESS.limit  # per-account trips
    await api_client.post("/auth/signup", json=_signup("victim@example.com"))
    guess = {"email": "victim@example.com", "password": "wrong-password-1"}

    statuses = [
        (await api_client.post("/auth/login", json=guess)).status_code
        for _ in range(rate_limit.LOGIN_EMAIL.limit + 1)
    ]

    assert statuses[:-1] == [401] * rate_limit.LOGIN_EMAIL.limit
    assert statuses[-1] == 429
    # Upper-casing the email is the same account, so it doesn't reset the count.
    shouted = {**guess, "email": "VICTIM@example.com"}
    assert (await api_client.post("/auth/login", json=shouted)).status_code == 429


async def test_reviews_are_limited_per_user(
    api_client: AsyncClient, limited: _CountingRedis
) -> None:
    signup = await api_client.post("/auth/signup", json=_signup("reviewer@example.com"))
    headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}
    await configure_review_model(api_client, headers)
    body = {
        "repo_full_name": "acme/widgets",
        "base_branch": "main",
        "head_sha": "abc1234",
        "pr_number": 1,
        "pr_title": "t",
        "pr_body": "",
        "diff": "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n",
    }

    limit = rate_limit.REVIEW.limit
    codes = [
        (await api_client.post("/analysis", json=body, headers=headers)).status_code
        for _ in range(limit + 1)
    ]

    assert codes == [202] * limit + [429]
    assert len(limited.enqueued) == limit  # the refused review was never queued


async def test_a_redis_outage_lets_requests_through(
    api_client: AsyncClient, limited: _CountingRedis
) -> None:
    limited.down = True
    for i in range(rate_limit.SIGNUP.limit + 2):
        response = await api_client.post("/auth/signup", json=_signup(f"o{i}@example.com"))
        assert response.status_code == 201


async def test_limits_are_kept_without_storing_emails_or_addresses(
    api_client: AsyncClient, limited: _CountingRedis
) -> None:
    await api_client.post("/auth/login", json={"email": "who@example.com", "password": "x" * 12})
    assert limited.counts
    assert not any("who@example.com" in key for key in limited.counts)
    assert not any("127.0.0.1" in key or "testclient" in key for key in limited.counts)
