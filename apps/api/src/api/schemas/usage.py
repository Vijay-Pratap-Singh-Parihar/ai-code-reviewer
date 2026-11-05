from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class BudgetPublic(BaseModel):
    # None: no cap of that kind.
    monthly_budget_usd: float | None
    token_cap: int | None
    warn_percent: int


class BudgetUpdate(BaseModel):
    """The whole budget (PUT): leave a cap out, or null, to remove it."""

    monthly_budget_usd: Decimal | None = Field(
        default=None, ge=0, le=1_000_000, max_digits=12, decimal_places=2
    )
    token_cap: int | None = Field(default=None, ge=1, le=2_000_000_000)
    warn_percent: int = Field(default=80, ge=1, le=100)


class UsageTotals(BaseModel):
    cost_usd: float
    tokens: int
    runs: int


class DailyUsage(UsageTotals):
    day: date


class MonthlyUsage(UsageTotals):
    month: str  # "2026-11"


class RepoUsage(UsageTotals):
    repo_full_name: str


class ModelUsage(UsageTotals):
    provider_name: str
    model: str


class UsagePublic(BaseModel):
    period_start: datetime
    resets_at: datetime
    spent_usd: float
    tokens: int
    runs: int
    budget: BudgetPublic
    # Of whichever cap is closer to being reached; None without a cap.
    used_percent: float | None
    status: Literal["ok", "warning", "exceeded"]
    daily: list[DailyUsage]
    months: list[MonthlyUsage]
    by_repo: list[RepoUsage]
    by_model: list[ModelUsage]
