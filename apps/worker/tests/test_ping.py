from worker.jobs.ping import ping


async def test_ping_returns_pong() -> None:
    assert await ping({}) == "pong"
