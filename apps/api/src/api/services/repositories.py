"""Look up or create an organisation's `Repository` rows.

Repositories are unique per organisation, not globally: two organisations
that review the same GitHub repository each get their own row, and with it
their own pull requests, runs, findings, clone and index. Every lookup here
is scoped to the caller's organisation explicitly, on top of the row-level
security policy that would hide other organisations' rows anyway.
"""

import uuid

from db.repository import Repository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def get_or_create_repository(
    session: AsyncSession, *, org_id: uuid.UUID, full_name: str
) -> Repository:
    repo = await session.scalar(
        select(Repository).where(Repository.org_id == org_id, Repository.full_name == full_name)
    )
    if repo is not None:
        return repo

    repo = Repository(org_id=org_id, full_name=full_name)
    session.add(repo)
    await session.flush()
    return repo


async def get_repository_for_org(
    session: AsyncSession, *, repo_id: uuid.UUID, org_id: uuid.UUID
) -> Repository | None:
    """The multi-tenancy enforcement point for endpoints addressed by
    `repo_id` (not `full_name`) — returns None (never another org's row) if
    the repo doesn't exist or belongs to a different org, the same "invisible
    rather than a leaked 403" convention `get_run_for_org` uses.
    """
    result: Repository | None = await session.scalar(
        select(Repository).where(Repository.id == repo_id, Repository.org_id == org_id)
    )
    return result
