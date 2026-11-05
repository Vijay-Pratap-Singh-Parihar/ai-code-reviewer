"""GET /metrics: what it exposes and who may read it."""

from collections.abc import Iterator

import pytest
from api.core.config import get_settings
from api.core.queue import get_redis_pool
from api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


class _Redis:
    async def zcard(self, key: str) -> int:
        assert key == "arq:queue"
        return 4


@pytest.fixture(autouse=True)
def redis() -> Iterator[None]:
    app.dependency_overrides[get_redis_pool] = lambda: _Redis()
    yield
    app.dependency_overrides.pop(get_redis_pool, None)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    current = get_settings()
    monkeypatch.setattr(current, "metrics_token", "")
    monkeypatch.setattr(current, "environment", "development")
    yield current


def _value(text: str, prefix: str) -> float:
    lines = [line for line in text.splitlines() if line.startswith(prefix)]
    assert lines, f"no series {prefix!r}"
    return float(lines[0].rsplit(" ", 1)[1])


def test_requests_are_counted_by_route_template_not_raw_path(settings: object) -> None:
    run_id = "6f0c6b9e-4e7b-4c55-9d8e-2a4f1f0f9a11"
    client.get(f"/analysis/{run_id}")  # 401, but routed

    text = client.get("/metrics").text

    assert 'route="/analysis/{run_id}"' in text
    assert run_id not in text
    assert "revu_http_request_duration_seconds_bucket" in text


def test_unknown_paths_share_one_label(settings: object) -> None:
    client.get("/wp-admin/setup.php")
    text = client.get("/metrics").text
    assert "wp-admin" not in text
    assert 'route="unmatched"' in text


def test_queue_depth_is_read_at_scrape_time(settings: object) -> None:
    text = client.get("/metrics").text
    assert _value(text, "revu_queue_depth") == 4


def test_a_token_is_required_once_one_is_set(
    settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "metrics_token", "scrape-secret-123")

    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/metrics", headers={"Authorization": "Bearer scrape-secret-123"})
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("text/plain")


def test_without_a_token_it_is_off_in_production(
    settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    assert client.get("/metrics").status_code == 404
