import uuid
from datetime import datetime
from typing import Literal

from db.pull_request import AnalysisRunStatus
from pydantic import BaseModel, Field


class GitHubAppInfo(BaseModel):
    configured: bool
    install_url: str | None
    webhook_configured: bool


class InstallationLinkRequest(BaseModel):
    """The two query parameters GitHub appends to the App's Setup URL. Both
    are required: `code` is how the server proves the signed-in user really
    can access `installation_id` (see `api.services.github`)."""

    installation_id: int = Field(ge=1)
    code: str = Field(min_length=1, max_length=256)


class InstallationPublic(BaseModel):
    id: uuid.UUID
    installation_id: int
    account_login: str
    account_type: str
    installed_at: datetime
    repository_count: int = 0


class RepositoryPublic(BaseModel):
    id: uuid.UUID
    full_name: str
    default_branch: str
    is_active: bool
    connected: bool
    auto_review_enabled: bool
    github_repo_id: int | None


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
