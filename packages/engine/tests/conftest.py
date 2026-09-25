"""Shared fixtures for packages/engine's test suite.

`real_repo_index` builds a real index of this repository's own working tree
once per test session. Building it (full tree-sitter parsing of 60+ files)
is expensive enough that several test modules independently doing it (Stage
4's hand-verified-edges check, Stage 6's and Stage 7's integration tests —
and, within Stage 6's own file, once per test function) was pure waste: nine
redundant full-repo builds across one test run before this fixture existed.
None of these tests mutate the graph, so sharing one built copy is safe.

`packages/engine/tests/index/test_timing.py` deliberately does *not* use
this — measuring a fresh build's own cold-start time is that test's entire
point, so it must build its own index rather than reuse a cached one.
"""

from pathlib import Path

import pytest
from revu.index.graph import IndexResult, build_index_at_path

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def real_repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def real_repo_index() -> IndexResult:
    return build_index_at_path(REPO_ROOT)
