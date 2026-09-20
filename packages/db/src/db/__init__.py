"""Shared SQLAlchemy models and declarative base for apps/api and apps/worker.

Per the data model in Product_Architecture_FullStack.md §5. Every model
module is imported here so `Base.metadata` is fully populated before Alembic
autogenerate (or `Base.metadata.create_all`) runs.
"""

from db.auth import RefreshToken
from db.branch_index import BranchIndex, IndexUpdateLog
from db.ledger import AuditLog, TokenUsageLedger
from db.organization import GithubInstallation, Organization, User
from db.provider import AIProvider, ModelRoute
from db.pull_request import AnalysisRun, ContextBundleRecord, FindingRecord, PullRequest
from db.repository import Repository, TrackedBranch

__all__ = [
    "AIProvider",
    "AnalysisRun",
    "AuditLog",
    "BranchIndex",
    "ContextBundleRecord",
    "FindingRecord",
    "GithubInstallation",
    "IndexUpdateLog",
    "ModelRoute",
    "Organization",
    "PullRequest",
    "RefreshToken",
    "Repository",
    "TokenUsageLedger",
    "TrackedBranch",
    "User",
]
