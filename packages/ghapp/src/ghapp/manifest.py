"""GitHub App manifest flow: let GitHub create a fully configured App.

Instead of a person filling in GitHub's "New GitHub App" form by hand,
revu builds a manifest describing the App (permissions, events, URLs) and
the browser POSTs it to GitHub. The user only confirms. GitHub then
redirects back with a one-time `code` that `GitHubClient.convert_manifest_code`
exchanges for the App's ID, private key, client secret and webhook secret.
See https://docs.github.com/apps/sharing-github-apps/registering-a-github-app-from-a-manifest
"""

from __future__ import annotations

import re
import secrets
from typing import Any

# Read-only by design: revu reads code and PRs; it doesn't write to repos.
PERMISSIONS = {"contents": "read", "pull_requests": "read", "metadata": "read"}
EVENTS = ["pull_request", "push"]

_ORG_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


def default_app_name() -> str:
    # App names are unique across all of GitHub; the user can still rename
    # it on GitHub's confirmation page.
    return f"revu-{secrets.token_hex(3)}"


def build_manifest(
    *, web_app_url: str, webhook_url: str, name: str | None = None
) -> dict[str, Any]:
    web = web_app_url.rstrip("/")
    return {
        "name": name or default_app_name(),
        "url": web,
        "description": "revu: agentic code review",
        "hook_attributes": {"url": webhook_url, "active": True},
        "redirect_url": f"{web}/github/app-created",
        "callback_urls": [f"{web}/github/setup"],
        # Makes GitHub send an OAuth `code` with each installation, which is
        # how revu verifies who installed it (see api.services.github).
        "request_oauth_on_install": True,
        "public": False,
        "default_permissions": PERMISSIONS,
        "default_events": EVENTS,
    }


def creation_url(*, web_url: str, state: str, organization: str | None = None) -> str:
    """Where the browser POSTs the manifest. An organization owns the App
    if given (the user must be an owner there); otherwise their account."""
    base = web_url.rstrip("/")
    if organization:
        if not _ORG_NAME.match(organization):
            raise ValueError(f"not a valid GitHub organization name: {organization!r}")
        return f"{base}/organizations/{organization}/settings/apps/new?state={state}"
    return f"{base}/settings/apps/new?state={state}"
