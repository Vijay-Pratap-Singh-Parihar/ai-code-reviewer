"""GitHub App authentication.

A GitHub App authenticates as itself with a short-lived RS256 JWT signed by
its private key, then exchanges that for a per-installation access token
(see `client.GitHubClient.create_installation_token`). Only the installation
token can read repositories; the App JWT can only manage the App itself.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt

# GitHub rejects App JWTs valid for more than 10 minutes, and recommends
# backdating `iat` to absorb clock drift between us and GitHub.
_CLOCK_DRIFT = timedelta(seconds=60)
_LIFETIME = timedelta(minutes=9)


class GitHubAppNotConfiguredError(RuntimeError):
    """Raised when App credentials are missing from configuration."""


def load_private_key(*, key_text: str = "", key_path: str = "") -> str:
    """Return the App's PEM private key from a file path or inline text.

    Inline text may use literal `\\n` escapes, since a multi-line PEM is
    awkward to put in a `.env` file.
    """
    if key_path:
        return Path(key_path).read_text(encoding="utf-8")
    if key_text:
        return key_text.replace("\\n", "\n")
    raise GitHubAppNotConfiguredError("no GitHub App private key configured")


def make_app_jwt(app_id: str, private_key_pem: str, *, now: datetime | None = None) -> str:
    if not app_id:
        raise GitHubAppNotConfiguredError("GITHUB_APP_ID is not set")
    issued = (now or datetime.now(UTC)) - _CLOCK_DRIFT
    payload = {
        "iat": int(issued.timestamp()),
        "exp": int((issued + _CLOCK_DRIFT + _LIFETIME).timestamp()),
        "iss": app_id,
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256")
