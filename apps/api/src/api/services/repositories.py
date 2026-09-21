"""Get-or-create a `Repository` row, shared by every trigger endpoint that
identifies a repository by `full_name` in its request body rather than by a
path-based `repo_id` — there is still no endpoint that creates a bare
`Repository` row on its own (no GitHub App yet, per Stage 10's deferral), so
every caller that needs one derives it from a name the same way.

Split out of `api.services.analysis` in Stage 5 when `api.services.branch_index`
needed the exact same logic; `analysis.py` re-exports both names for backward
compatibility with its existing tests and callers.
"""

import uuid

from db.repository import Repository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class RepositoryOwnedByAnotherOrgError(Exception):
    """`repositories.full_name` is globally unique because in the real
    GitHub App model (Stage 10), a given repo can only be connected to one
    org's installation. Until then, ad-hoc endpoints have no GitHub
    verification to enforce that naturally, so it must be checked explicitly
    — silently reusing another org's row would misattribute a run/index to
    the wrong org in a way that's invisible until the caller tries to fetch
    it back and gets a confusing 404.
    """


async def get_or_create_repository(
    session: AsyncSession, *, org_id: uuid.UUID, full_name: str
) -> Repository:
    repo = await session.scalar(select(Repository).where(Repository.full_name == full_name))
    if repo is not None:
        if repo.org_id != org_id:
            raise RepositoryOwnedByAnotherOrgError(full_name)
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
