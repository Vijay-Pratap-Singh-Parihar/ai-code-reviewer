"""A local git cache of GitHub repositories, for the jobs that need real
files on disk (index builds and cross-file reviews).

Authentication never touches the URL or `.git/config`. The installation
token goes in an `Authorization` header through git's `GIT_CONFIG_*`
environment variables, so it is not in the process's argv, in the cached
repo's config, or in any error message git prints (which echo the URL).
Tokens expire after an hour, so persisting one would be useless anyway.
"""

from __future__ import annotations

import asyncio
import base64
import os
import subprocess
from collections import defaultdict
from pathlib import Path

# One fetch at a time per cached repo: concurrent `git fetch`es into the
# same directory fail on git's ref locks. (One worker process per compose
# deployment; multiple worker processes would need a file lock instead.)
_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


class RepoCacheError(RuntimeError):
    pass


def _git_env(token: str | None) -> dict[str, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update(
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.extraHeader",
                "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
            }
        )
    return env


def _run_git(args: list[str], *, cwd: Path, token: str | None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=_git_env(token),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip()
        if token:
            message = message.replace(token, "***")
        raise RepoCacheError(f"git {args[0]} failed: {message}")
    return result.stdout.strip()


def _fetch_sync(dest: Path, remote_url: str, token: str | None, refspecs: list[str]) -> None:
    if not (dest / ".git").exists():
        dest.mkdir(parents=True, exist_ok=True)
        _run_git(["init", "--quiet"], cwd=dest, token=None)
    _run_git(
        ["fetch", "--quiet", "--no-tags", "--force", remote_url, *refspecs], cwd=dest, token=token
    )


async def fetch_refs(
    *, dest: Path, remote_url: str, token: str | None, refspecs: list[str]
) -> Path:
    """Fetch `refspecs` from `remote_url` into the cached repo at `dest`
    (creating it on first use) and return its path. `dest` comes from
    `worker.storage.TenantStorage.repo_cache_dir`, one per repository."""
    async with _locks[str(dest)]:
        await asyncio.to_thread(_fetch_sync, dest, remote_url, token, refspecs)
    return dest


async def rev_parse(repo_path: Path, ref: str) -> str:
    return await asyncio.to_thread(
        _run_git, ["rev-parse", "--verify", ref], cwd=repo_path, token=None
    )


def branch_refspec(branch: str) -> str:
    return f"+refs/heads/{branch}:refs/remotes/origin/{branch}"


def pull_refspec(number: int) -> str:
    return f"+refs/pull/{number}/head:refs/remotes/origin/pr/{number}"
