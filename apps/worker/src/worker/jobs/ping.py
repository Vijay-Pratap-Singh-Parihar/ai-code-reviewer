from typing import Any


async def ping(ctx: dict[str, Any]) -> str:
    """Trivial job used to verify the worker can connect to Redis and run jobs."""
    return "pong"
