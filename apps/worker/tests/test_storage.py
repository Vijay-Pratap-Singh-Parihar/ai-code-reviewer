"""Tenant storage layout: every repository's clone and index blobs live in
a directory keyed by its organisation's and its own UUID."""

import uuid
from pathlib import Path

import pytest
from worker.storage import StorageScopeError, TenantStorage, signing_key_from_secret


@pytest.fixture
def storage(tmp_path: Path) -> TenantStorage:
    return TenantStorage(
        repo_cache_root=tmp_path / "repos",
        index_root=tmp_path / "index",
        signing_key=signing_key_from_secret("s"),
    )


def test_each_repository_gets_its_own_directories(storage: TenantStorage) -> None:
    """Two organisations connecting the same GitHub repo (same name) still
    get separate clones and separate index blobs."""
    org_a, org_b, repo_a, repo_b = (uuid.uuid4() for _ in range(4))

    assert storage.repo_cache_dir(org_a, repo_a) != storage.repo_cache_dir(org_b, repo_b)
    assert storage.index_dir(org_a, repo_a) != storage.index_dir(org_b, repo_b)
    assert storage.repo_cache_dir(org_a, repo_a) == (
        storage.repo_cache_root / str(org_a) / str(repo_a)
    )


def test_require_index_path_accepts_a_blob_in_the_repos_own_directory(
    storage: TenantStorage,
) -> None:
    org, repo = uuid.uuid4(), uuid.uuid4()
    blob = storage.index_dir(org, repo) / "abc.graph.pkl.gz"

    assert storage.require_index_path(blob, org, repo) == blob.resolve()


@pytest.mark.parametrize(
    "make_path",
    [
        # another repository of the same organisation
        lambda s, org, repo: s.index_dir(org, uuid.uuid4()) / "x.graph.pkl.gz",
        # another organisation
        lambda s, org, repo: s.index_dir(uuid.uuid4(), repo) / "x.graph.pkl.gz",
        # a pre-namespacing blob at the root of the shared directory
        lambda s, org, repo: s.index_root / "x.graph.pkl.gz",
        # traversal out of the repository's directory
        lambda s, org, repo: s.index_dir(org, repo) / ".." / "other" / "x.graph.pkl.gz",
    ],
)
def test_require_index_path_refuses_anything_else(storage: TenantStorage, make_path) -> None:  # type: ignore[no-untyped-def]
    org, repo = uuid.uuid4(), uuid.uuid4()

    with pytest.raises(StorageScopeError):
        storage.require_index_path(make_path(storage, org, repo), org, repo)


def test_signing_key_is_derived_not_the_raw_secret() -> None:
    key = signing_key_from_secret("deployment-secret")

    assert key == signing_key_from_secret("deployment-secret")
    assert key != signing_key_from_secret("another-secret")
    assert b"deployment-secret" not in key
    assert len(key) == 32
