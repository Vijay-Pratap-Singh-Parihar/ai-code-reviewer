from typing import Any

from sqlalchemy import text


async def db_ping(ctx: dict[str, Any]) -> str:
    """Trivial job used to verify the worker's DB connection, set up in
    `WorkerSettings.on_startup`, actually works.
    """
    session_factory = ctx["db_session_factory"]
    async with session_factory() as session:
        result = await session.execute(text("SELECT 1"))
        return "ok" if result.scalar_one() == 1 else "unexpected result"
