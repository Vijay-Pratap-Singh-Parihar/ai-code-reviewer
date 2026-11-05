"""What the Usage & Budget page shows: this month against the budget, day
by day, by repository and by model, and the months before. Everything is
summed from `token_usage_ledger` (see `db.usage`)."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from db.audit import AuditAction, record_audit
from db.ledger import TokenUsageLedger
from db.organization import Organization, User
from db.pull_request import AnalysisRun, PullRequest
from db.repository import Repository
from db.usage import budget_state
from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas.usage import (
    BudgetPublic,
    BudgetUpdate,
    DailyUsage,
    ModelUsage,
    MonthlyUsage,
    RepoUsage,
    UsagePublic,
)

__all__ = ["budget_public", "get_usage", "set_budget"]

MONTHS_SHOWN = 6
TOP_N = 10

_COST = func.coalesce(func.sum(TokenUsageLedger.cost_usd), 0)
_TOKENS = func.coalesce(func.sum(TokenUsageLedger.tokens_in + TokenUsageLedger.tokens_out), 0)
_RUNS = func.count(TokenUsageLedger.id)


def _totals(cost: Any, tokens: Any, runs: Any) -> dict[str, Any]:
    return {"cost_usd": float(cost), "tokens": int(tokens), "runs": int(runs)}


def _months_back(start: datetime, n: int) -> datetime:
    year, month = start.year, start.month - n
    while month < 1:
        year, month = year - 1, month + 12
    return start.replace(year=year, month=month)


def budget_public(org: Organization) -> BudgetPublic:
    return BudgetPublic(
        monthly_budget_usd=(
            float(org.monthly_budget_usd) if org.monthly_budget_usd is not None else None
        ),
        token_cap=org.token_quota_monthly or None,
        warn_percent=org.budget_warn_percent,
    )


async def get_usage(
    session: AsyncSession, org_id: uuid.UUID, *, now: datetime | None = None
) -> UsagePublic:
    now = now or datetime.now(UTC)
    state = await budget_state(session, org_id, now=now)
    in_period = (
        TokenUsageLedger.org_id == org_id,
        TokenUsageLedger.occurred_at >= state.period_start,
        TokenUsageLedger.occurred_at < state.resets_at,
    )

    # Day by day, every day of the month so far (days without reviews too).
    day_col = cast(func.timezone("UTC", TokenUsageLedger.occurred_at), Date)
    day_rows = await session.execute(
        select(day_col, _COST, _TOKENS, _RUNS).where(*in_period).group_by(day_col)
    )
    by_day = {d: _totals(c, t, r) for d, c, t, r in day_rows.all()}
    first: date = state.period_start.date()
    days = (now.astimezone(UTC).date() - first).days + 1
    daily = [
        DailyUsage(day=d, **by_day.get(d, _totals(0, 0, 0)))
        for d in (first + timedelta(days=i) for i in range(days))
    ]

    # This month and the ones before it, oldest first.
    since = _months_back(state.period_start, MONTHS_SHOWN - 1)
    month_col = func.to_char(func.timezone("UTC", TokenUsageLedger.occurred_at), "YYYY-MM")
    month_rows = await session.execute(
        select(month_col, _COST, _TOKENS, _RUNS)
        .where(
            TokenUsageLedger.org_id == org_id,
            TokenUsageLedger.occurred_at >= since,
            TokenUsageLedger.occurred_at < state.resets_at,
        )
        .group_by(month_col)
    )
    by_month = {m: _totals(c, t, r) for m, c, t, r in month_rows.all()}
    months = [
        MonthlyUsage(month=key, **by_month.get(key, _totals(0, 0, 0)))
        for key in (
            f"{_months_back(state.period_start, i):%Y-%m}" for i in range(MONTHS_SHOWN - 1, -1, -1)
        )
    ]

    repo_rows = await session.execute(
        select(Repository.full_name, _COST, _TOKENS, _RUNS)
        .select_from(TokenUsageLedger)
        .join(AnalysisRun, AnalysisRun.id == TokenUsageLedger.run_id)
        .join(PullRequest, PullRequest.id == AnalysisRun.pr_id)
        .join(Repository, Repository.id == PullRequest.repo_id)
        .where(*in_period)
        .group_by(Repository.full_name)
        .order_by(_COST.desc(), _TOKENS.desc())
        .limit(TOP_N)
    )
    by_repo = [RepoUsage(repo_full_name=n, **_totals(c, t, r)) for n, c, t, r in repo_rows.all()]

    # The model a run used is in its config snapshot, as it was then.
    provider_col = AnalysisRun.config_snapshot["provider_name"].astext
    model_col = AnalysisRun.config_snapshot["model"].astext
    model_rows = await session.execute(
        select(provider_col, model_col, _COST, _TOKENS, _RUNS)
        .select_from(TokenUsageLedger)
        .join(AnalysisRun, AnalysisRun.id == TokenUsageLedger.run_id)
        .where(*in_period)
        .group_by(provider_col, model_col)
        .order_by(_COST.desc(), _TOKENS.desc())
        .limit(TOP_N)
    )
    by_model = [
        ModelUsage(provider_name=p or "unknown", model=m or "unknown", **_totals(c, t, r))
        for p, m, c, t, r in model_rows.all()
    ]

    org = await session.get(Organization, org_id)
    assert org is not None  # budget_state found it
    used = state.used_fraction
    return UsagePublic(
        period_start=state.period_start,
        resets_at=state.resets_at,
        spent_usd=float(state.spent_usd),
        tokens=state.tokens,
        runs=state.runs,
        budget=budget_public(org),
        used_percent=round(used * 100, 1) if used is not None else None,
        status=state.status.value,
        daily=daily,
        months=months,
        by_repo=by_repo,
        by_model=by_model,
    )


async def set_budget(session: AsyncSession, *, user: User, body: BudgetUpdate) -> BudgetPublic:
    org = await session.get(Organization, user.org_id)
    if org is None:
        raise LookupError(f"organization {user.org_id} not found")
    before = budget_public(org)
    org.monthly_budget_usd = body.monthly_budget_usd
    org.token_quota_monthly = body.token_cap or 0
    org.budget_warn_percent = body.warn_percent
    after = budget_public(org)
    if after != before:
        record_audit(
            session,
            org_id=org.id,
            actor_id=user.id,
            action=AuditAction.BUDGET_CHANGED,
            target=f"organization:{org.id}",
            metadata={"before": before.model_dump(), "after": after.model_dump()},
        )
    await session.commit()
    return after
