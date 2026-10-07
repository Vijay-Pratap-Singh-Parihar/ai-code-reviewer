import uuid
from datetime import datetime
from typing import Any, Literal

from db.pull_request import AnalysisRunStatus
from pydantic import BaseModel, Field


class GitHubAppInfo(BaseModel):
    configured: bool
    install_url: str | None
    webhook_configured: bool
    source: Literal["env", "database"] | None = None
    slug: str | None = None
    app_url: str | None = None
    # The smee.io channel webhooks arrive through in local development.
    webhook_proxy_url: str | None = None
    # Set when credentials exist but can't be used (e.g. undecryptable).
    error: str | None = None


class ManifestStartRequest(BaseModel):
    organization: str | None = Field(
        default=None,
        max_length=39,
        description="GitHub organization to own the App; defaults to your personal account.",
    )


class ManifestStartResponse(BaseModel):
    """POST `manifest` (JSON-encoded, as a form field named `manifest`) to
    `action_url` from the browser; GitHub takes it from there."""

    action_url: str
    manifest: dict[str, Any]


class ManifestCompleteRequest(BaseModel):
    code: str = Field(min_length=1, max_length=256)
    state: str = Field(min_length=1, max_length=4096)


class InstallationLinkRequest(BaseModel):
    """The two query parameters GitHub appends to the App's Setup URL. Both
    are required: `code` is how the server proves the signed-in user really
    can access `installation_id` (see `api.services.github`)."""

    installation_id: int = Field(ge=1)
    code: str = Field(min_length=1, max_length=256)


ConnectionStatus = Literal["active", "suspended", "uninstalled"]


class InstallationPublic(BaseModel):
    id: uuid.UUID
    installation_id: int
    account_login: str
    account_type: str
    installed_at: datetime
    repository_count: int = 0
    status: ConnectionStatus = "active"
    suspended_at: datetime | None = None
    uninstalled_at: datetime | None = None
    # Repositories under this installation that are disconnected and waiting
    # for the retention job, and when the earliest of them is purged.
    disconnected_repository_count: int = 0
    purge_after: datetime | None = None


class RepositoryPublic(BaseModel):
    id: uuid.UUID
    full_name: str
    default_branch: str
    is_active: bool
    connected: bool
    auto_review_enabled: bool
    github_repo_id: int | None
    installation_id: uuid.UUID | None = None
    disconnected_at: datetime | None = None
    # When the retention job deletes this repository's data, if disconnected.
    purge_after: datetime | None = None


class RepositoryUpdate(BaseModel):
    auto_review_enabled: bool


class LatestRunPublic(BaseModel):
    id: uuid.UUID
    status: AnalysisRunStatus
    agent: str
    head_sha: str | None


class PullRequestSummary(BaseModel):
    number: int
    title: str
    author: str
    head_sha: str
    base_branch: str
    draft: bool
    html_url: str | None
    updated_at: datetime | None
    latest_run: LatestRunPublic | None = None


class GitHubReviewRequest(BaseModel):
    """Same cost/depth choice as `AnalysisRequest.agent`; `cross_file`
    additionally needs a ready index for the PR's base branch."""

    agent: Literal["diff_only", "cross_file"] = "diff_only"


class GitHubIndexRequest(BaseModel):
    branch_name: str | None = Field(
        default=None, max_length=255, description="Defaults to the repository's default branch."
    )
    force_full: bool = False


class WebhookResponse(BaseModel):
    status: str
    detail: str = ""
    enqueued: list[str] = Field(default_factory=list)
