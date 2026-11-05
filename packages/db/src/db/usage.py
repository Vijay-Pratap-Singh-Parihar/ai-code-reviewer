"""Monthly usage and the budget that caps it, shared by the API and the worker.

An organisation's month is the calendar month in UTC. What it has used is
summed from `token_usage_ledger` (one row per finished run), not kept as a
running counter, so it can't drift. Two caps, either optional:

* spend, `Organization.monthly_budget_usd` (None = no cap);
* tokens, `Organization.token_quota_monthly` (0 = no cap), for local models,
  which report no cost.

From `budget_warn_percent` of either cap the app warns; once either is
reached, new reviews are refused (`ensure_within_budget`) until the next
month or a higher cap. A review already running when the cap is reached
finishes, so a month can end slightly over; the cap stops the next one.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.ledger import TokenUsageLedger
from db.organization import Organization
from db.pull_request import AnalysisRun

__all__ = [
    "BudgetExceededError",
    "BudgetState",
    "BudgetStatus",
    "budget_state",
    "ensure_within_budget",
    "period_bounds",
    "record_run_usage",
]


class BudgetStatus(enum.StrEnum):
    OK = "ok"
    WARNING = "warning"
    EXCEEDED = "exceeded"


def period_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """The start of this month and of the next, in UTC."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, nxt


@dataclass(frozen=True)
class BudgetState:
    period_start: datetime
    resets_at: datetime
    spent_usd: Decimal
    tokens: int
    runs: int
    budget_usd: Decimal | None
    token_cap: int | None
    warn_percent: int

    @property
    def used_fraction(self) -> float | None:
        """The larger share of either cap used, or None without a cap."""
        shares = []
        if self.budget_usd is not None:
            shares.append(float(self.spent_usd / self.budget_usd) if self.budget_usd else 1.0)
        if self.token_cap:
            shares.append(self.tokens / self.token_cap)
        return max(shares) if shares else None

    @property
    def status(self) -> BudgetStatus:
        used = self.used_fraction
        if used is None:
            return BudgetStatus.OK
        if used >= 1:
            return BudgetStatus.EXCEEDED
        if used * 100 >= self.warn_percent:
            return BudgetStatus.WARNING
        return BudgetStatus.OK

    def describe(self) -> str:
        """Why reviews are refused, for the person who asked for one."""
        if self.budget_usd is not None and self.spent_usd >= self.budget_usd:
            reached = f"its monthly budget of ${self.budget_usd:,.2f}"
        else:
            reached = f"its monthly limit of {self.token_cap:,} tokens"
        return (
            f"Your organization has reached {reached}. New reviews resume on "
            f"{self.resets_at:%d %B %Y}, or an owner or admin can raise the limit "
            "on the Usage & Budget page."
        )


class BudgetExceededError(Exception):
    def __init__(self, state: BudgetState) -> None:
        super().__init__(state.describe())
        self.state = state


async def budget_state(
    session: AsyncSession, org_id: uuid.UUID, *, now: datetime | None = None
) -> BudgetState:
    start, resets_at = period_bounds(now)
    org = await session.get(Organization, org_id)
    if org is None:
        raise LookupError(f"organization {org_id} not found")
    cost, tokens, runs = (
        await session.execute(
            select(
                func.coalesce(func.sum(TokenUsageLedger.cost_usd), 0),
                func.coalesce(
                    func.sum(TokenUsageLedger.tokens_in + TokenUsageLedger.tokens_out), 0
                ),
                func.count(TokenUsageLedger.id),
            ).where(
                TokenUsageLedger.org_id == org_id,
                TokenUsageLedger.occurred_at >= start,
                TokenUsageLedger.occurred_at < resets_at,
            )
        )
    ).one()
    return BudgetState(
        period_start=start,
        resets_at=resets_at,
        spent_usd=Decimal(cost),
        tokens=int(tokens),
        runs=int(runs),
        budget_usd=org.monthly_budget_usd,
        token_cap=org.token_quota_monthly or None,
        warn_percent=org.budget_warn_percent,
    )


async def ensure_within_budget(session: AsyncSession, org_id: uuid.UUID) -> BudgetState:
    """Raise `BudgetExceededError` once this month's cap is reached."""
    state = await budget_state(session, org_id)
    if state.status is BudgetStatus.EXCEEDED:
        raise BudgetExceededError(state)
    return state


def record_run_usage(session: AsyncSession, run: AnalysisRun) -> None:
    """Add the finished run's usage to the ledger; it commits with the run.
    Every finished run gets a row (even at zero), so the month's run count
    is right too."""
    session.add(
        TokenUsageLedger(
            org_id=run.org_id,
            run_id=run.id,
            tokens_in=run.tokens_in or 0,
            tokens_out=run.tokens_out or 0,
            cost_usd=run.cost_usd or 0,
            occurred_at=run.finished_at or datetime.now(UTC),
        )
    )
