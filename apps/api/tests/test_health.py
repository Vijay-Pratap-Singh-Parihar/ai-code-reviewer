"""Liveness, readiness and the request id every response carries."""

from collections.abc import Iterator

import pytest
from api.core.queue import enqueue, get_redis_pool
from api.main import app
from api.routers import health
from fastapi.testclient import TestClient
from observability import bound

client = TestClient(app)


class _Redis:
    def __init__(self, *, up: bool = True, worker: bool = True) -> None:
        self.up = up
        self.worker = worker

    async def ping(self) -> bool:
        if not self.up:
            raise ConnectionError("redis down")
        return True

    async def exists(self, key: str) -> int:
        assert key == health.WORKER_HEALTH_KEY
        return int(self.worker)


@pytest.fixture
def redis() -> Iterator[_Redis]:
    fake = _Redis()
    app.dependency_overrides[get_redis_pool] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_redis_pool, None)


@pytest.fixture
def database_up(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, bool]]:
    state = {"up": True}

    async def probe() -> None:
        if not state["up"]:
            raise OSError("connection refused")

    monkeypatch.setattr(health, "_database", probe)
    yield state


def test_liveness_answers_on_both_paths() -> None:
    for path in ("/health", "/health/live"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_ready_when_database_and_redis_answer(redis: _Redis, database_up: dict[str, bool]) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "redis": "ok"},
        "worker": "ok",
    }


def test_not_ready_without_the_database(redis: _Redis, database_up: dict[str, bool]) -> None:
    database_up["up"] = False

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "unavailable", "redis": "ok"}


def test_not_ready_without_redis(redis: _Redis, database_up: dict[str, bool]) -> None:
    redis.up = False

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "unavailable"
    assert response.json()["worker"] == "unknown"


def test_a_missing_worker_is_reported_but_does_not_take_the_api_out(
    redis: _Redis, database_up: dict[str, bool]
) -> None:
    redis.worker = False

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["worker"] == "not seen"


def test_every_response_carries_a_request_id_and_keeps_a_callers() -> None:
    generated = client.get("/health").headers["X-Request-ID"]
    kept = client.get("/health", headers={"X-Request-ID": "lb-7f3a"}).headers["X-Request-ID"]
    replaced = client.get("/health", headers={"X-Request-ID": "bad id\twith spaces"})

    assert len(generated) == 32
    assert kept == "lb-7f3a"
    assert replaced.headers["X-Request-ID"] != "bad id\twith spaces"
    assert client.get("/no-such-route").headers["X-Request-ID"]  # errors too


async def test_a_queued_job_carries_the_request_id() -> None:
    sent: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    class Recorder:
        async def enqueue_job(self, function: str, *args: object, **kwargs: object) -> None:
            sent.append((function, args, kwargs))

    with bound(request_id="req-42"):
        await enqueue(Recorder(), "analyze_pr", "run-1", "org-1")  # type: ignore[arg-type]

    assert sent == [("analyze_pr", ("run-1", "org-1"), {"request_id": "req-42"})]
