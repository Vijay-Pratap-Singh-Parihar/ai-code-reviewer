"""AACR-Bench (Zhang et al., arXiv 2601.19494; github.com/alibaba/aacr-bench,
Apache-2.0) into `BenchInstance` records.

The release has no diffs: each record names a pull request, its base
(`source_commit`) and head (`target_commit`) commits, and the expert-verified
reference comments. The diff is GitHub's three-dot comparison of the two
commits (from their merge base), which reproduces the release's
`change_line_count`; the PR title and description come from the pull request
itself, since revu reviews with them in production. Both are fetched once
and cached on disk, so conversion is repeatable offline.

Instance ids follow AACR's own converter (`owner__name@<head[:7]>`) so
results can be compared with theirs record by record.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from revu_bench.schema import BenchInstance, ContextLevel, GroundTruthComment

# The release this harness was built against. A new release must be
# inspected (and these updated) on purpose, never picked up silently.
RELEASE_COMMIT = "ee9d7fef70ef31acea2ff7ae316ce0a9575fe4df"
POSITIVE_URL = (
    f"https://raw.githubusercontent.com/alibaba/aacr-bench/{RELEASE_COMMIT}"
    "/dataset/positive_samples.json"
)
POSITIVE_SHA256 = "02c8561f1c3994a26f427d479b2a3f6e6c8e6858161fbc903b45248fa2cb2097"

_PR_URL = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", re.IGNORECASE)
_CONTEXT: dict[str, ContextLevel] = {
    "diff level": "diff",
    "file level": "file",
    "repo level": "repo",
}


@dataclass(frozen=True)
class PullRequestRef:
    owner: str
    name: str
    number: int
    base: str
    head: str

    @property
    def repo(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def instance_id(self) -> str:
        return f"{self.owner}__{self.name}@{self.head[:7]}"


@dataclass(frozen=True)
class PullRequestData:
    diff: str
    title: str = ""
    body: str = ""


Fetcher = Callable[[PullRequestRef], PullRequestData]


def parse_ref(record: dict[str, Any]) -> PullRequestRef | None:
    match = _PR_URL.search(str(record.get("githubPrUrl") or ""))
    base, head = record.get("source_commit"), record.get("target_commit")
    if not match or not base or not head:
        return None
    return PullRequestRef(
        owner=match.group(1),
        name=match.group(2),
        number=int(match.group(3)),
        base=str(base).strip(),
        head=str(head).strip(),
    )


def _line(value: Any) -> int | None:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def ground_truth(record: dict[str, Any]) -> list[GroundTruthComment]:
    """The reference comments, AI-drafted and human alike (all are expert
    verified in the release). Comments without text or a file are dropped,
    as AACR's own converter does."""
    comments: list[GroundTruthComment] = []
    for raw in record.get("comments") or []:
        if not isinstance(raw, dict):
            continue
        path, text = str(raw.get("path") or "").strip(), str(raw.get("note") or "").strip()
        if not path or not text:
            continue
        start = _line(raw.get("from_line"))
        end = _line(raw.get("to_line"))
        start = start if start is not None else end or 0
        end = end if end is not None else start
        comments.append(
            GroundTruthComment(
                path=path,
                line_start=min(start, end),
                line_end=max(start, end),
                text=text,
                category=str(raw.get("category") or "").strip() or None,
                context_level=_CONTEXT.get(str(raw.get("context") or "").strip().lower()),
            )
        )
    return comments


def convert(
    records: Iterable[dict[str, Any]],
    fetch: Fetcher,
    on_error: Callable[[PullRequestRef, Exception], None] | None = None,
) -> list[BenchInstance]:
    """Release records -> instances. Records that don't name a pull request
    and both commits are skipped; so are duplicates of an id already seen.
    A pull request that can't be fetched raises, unless `on_error` is given:
    then it is reported there and left out."""
    instances: list[BenchInstance] = []
    seen: set[str] = set()
    for record in records:
        ref = parse_ref(record)
        if ref is None or ref.instance_id in seen:
            continue
        seen.add(ref.instance_id)
        try:
            data = fetch(ref)
        except Exception as exc:
            if on_error is None:
                raise
            on_error(ref, exc)
            continue
        instances.append(
            BenchInstance(
                id=ref.instance_id,
                repo=ref.repo,
                language=str(record.get("project_main_language") or "unknown").strip().lower(),
                base_sha=ref.base,
                head_sha=ref.head,
                pr_title=data.title,
                pr_body=data.body,
                diff=data.diff,
                ground_truth=ground_truth(record),
            )
        )
    return instances


# --- fetching -----------------------------------------------------------------


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download_release(target: Path) -> Path:
    """The pinned `positive_samples.json`, verified by checksum."""
    if not (target.exists() and sha256_of(target) == POSITIVE_SHA256):
        response = httpx.get(POSITIVE_URL, timeout=60.0, follow_redirects=True)
        response.raise_for_status()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.content)
    if sha256_of(target) != POSITIVE_SHA256:
        raise ValueError(f"{target}: checksum doesn't match AACR-Bench @ {RELEASE_COMMIT[:7]}")
    return target


def github_token() -> str | None:
    """`GITHUB_TOKEN`, else the GitHub CLI's login: 196 PRs need two calls
    each, well over the 60 an hour GitHub allows without a token."""
    if token := os.environ.get("GITHUB_TOKEN"):
        return token
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


class GitHubFetcher:
    """Fetches each PR's diff and description from the GitHub API, caching
    both under `cache_dir` so later conversions make no network calls."""

    def __init__(self, cache_dir: Path, token: str | None = None) -> None:
        self.cache_dir = cache_dir
        headers = {"X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        # Renamed repositories answer with a redirect to their new id.
        self.client = httpx.Client(
            base_url="https://api.github.com", headers=headers, timeout=60.0, follow_redirects=True
        )

    def __call__(self, ref: PullRequestRef) -> PullRequestData:
        stem = self.cache_dir / f"{ref.owner}__{ref.name}@{ref.base[:7]}..{ref.head[:7]}"
        diff_path, meta_path = stem.with_suffix(".diff"), stem.with_suffix(".json")
        if not diff_path.exists():
            response = self.client.get(
                f"/repos/{ref.repo}/compare/{ref.base}...{ref.head}",
                headers={"Accept": "application/vnd.github.diff"},
            )
            response.raise_for_status()
            diff_path.parent.mkdir(parents=True, exist_ok=True)
            # Bytes, so Windows doesn't rewrite the diff's line endings.
            diff_path.write_bytes(response.content)
        if not meta_path.exists():
            response = self.client.get(f"/repos/{ref.repo}/pulls/{ref.number}")
            response.raise_for_status()
            pr = response.json()
            meta = {"title": pr.get("title") or "", "body": pr.get("body") or ""}
            meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return PullRequestData(
            diff=diff_path.read_bytes().decode("utf-8", errors="replace"),
            title=meta["title"],
            body=meta["body"],
        )
