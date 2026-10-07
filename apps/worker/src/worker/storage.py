"""Where tenant data lives on disk, in one place.

Every repository gets its own directory under each root, keyed by the
owning organisation's and the repository's UUIDs:

    <repo_cache_root>/<org_id>/<repo_id>/   git cache (clone)
    <index_root>/<org_id>/<repo_id>/        signed index blobs

UUIDs, not `owner/name`: names are chosen by users, can be renamed, and two
organisations may connect the same GitHub repository as separate, isolated
copies. Nothing here ever takes a path from a request or a queue argument;
jobs derive paths from the rows they load.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class StorageScopeError(RuntimeError):
    """A stored path points outside the directory its row's tenant owns."""


def signing_key_from_secret(secret: str) -> bytes:
    """Derive the index-blob signing key from the deployment's credential
    encryption secret, so the two never share raw key material."""
    return hmac.new(secret.encode(), b"revu-index-blob-signing-v1", hashlib.sha256).digest()


@dataclass(frozen=True)
class TenantStorage:
    repo_cache_root: Path
    index_root: Path
    signing_key: bytes

    def repo_cache_dir(self, org_id: uuid.UUID, repo_id: uuid.UUID) -> Path:
        return self.repo_cache_root / str(org_id) / str(repo_id)

    def index_dir(self, org_id: uuid.UUID, repo_id: uuid.UUID) -> Path:
        return self.index_root / str(org_id) / str(repo_id)

    def require_index_path(self, path: Path, org_id: uuid.UUID, repo_id: uuid.UUID) -> Path:
        """Return `path` resolved, or raise if it is not inside this repo's
        index directory (e.g. a `graph_ref` written before storage was
        namespaced, or a row pointing at another tenant's blob)."""
        resolved = path.resolve()
        if not resolved.is_relative_to(self.index_dir(org_id, repo_id).resolve()):
            raise StorageScopeError(
                f"{path} is outside the index directory of repository {repo_id}; "
                "rebuild the branch index"
            )
        return resolved


def storage_from_ctx(ctx: dict[str, Any]) -> TenantStorage:
    storage: TenantStorage = ctx["storage"]
    return storage
