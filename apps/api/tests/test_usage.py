"""Usage & Budget: the month's usage from the ledger, the caps, and reviews
refused once a cap is reached."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from api.core.security import decode_access_token
from api.db.session import get_db
from api.main import app
from api.testing import configure_review_model
from db.ledger import AuditLog
from db.organization import User, UserRole
from db.pull_request import AnalysisRun, AnalysisRunStatus
from db.tenancy import bind_org
from db.usage import BudgetState, BudgetStatus, period_bounds, record_run_usage
from httpx import AsyncClient
from sqlalchemy import select

DIFF = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"


async def _signup(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/auth/signup",
        json={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "org_name": "Acme Inc",
            "email": email,
            "password": "correct-horse-battery",
        },
    )
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    await configure_review_model(client, headers)
    return headers


def _review(pr_number: int = 1) -> dict[str, object]:
    return {
        "repo_full_name": "acme/widgets",
        "base_branch": "main",
        "head_sha": "abc1234",
        "pr_number": pr_number,
        "pr_title": "Fix off-by-one",
        "pr_body": "",
        "diff": DIFF,
    }


def _org(headers: dict[str, str]) -> uuid.UUID:
    return decode_access_token(headers["Authorization"].removeprefix("Bearer ")).org_id


async def _finish(
    headers: dict[str, str], run_id: str, *, tokens_in: int, tokens_out: int, cost: str
) -> None:
    """Do what the worker does when a review succeeds."""
    async for session in app.dependency_overrides[get_db]():
        await bind_org(session, _org(headers))
        run = await session.get(AnalysisRun, uuid.UUID(run_id))
        assert run is not None
        run.status = AnalysisRunStatus.SUCCEEDED
        run.tokens_in, run.tokens_out, run.cost_usd = tokens_in, tokens_out, Decimal(cost)  # type: ignore[assignment]
        run.finished_at = datetime.now(UTC)
        record_run_usage(session, run)
        await session.commit()
        break


async def _reviewed(
    client: AsyncClient, headers: dict[str, str], *, tokens: int = 1000, cost: str = "0.50"
) -> str:
    created = await client.post("/analysis", json=_review(), headers=headers)
    assert created.status_code == 202, created.text
    run_id: str = created.json()["id"]
    await _finish(headers, run_id, tokens_in=tokens - 200, tokens_out=200, cost=cost)
    return run_id


async def test_a_new_organization_has_no_usage_and_no_caps(api_client: AsyncClient) -> None:
    headers = await _signup(api_client, "fresh@example.com")

    usage = (await api_client.get("/usage", headers=headers)).json()

    assert (usage["spent_usd"], usage["tokens"], usage["runs"]) == (0, 0, 0)
    assert usage["status"] == "ok" and usage["used_percent"] is None
    assert usage["budget"] == {"monthly_budget_usd": None, "token_cap": None, "warn_percent": 80}
    assert len(usage["months"]) == 6
    assert usage["months"][-1]["month"] == f"{datetime.now(UTC):%Y-%m}"
    assert len(usage["daily"]) == datetime.now(UTC).day


async def test_finished_reviews_add_up_by_day_repository_and_model(api_client: AsyncClient) -> None:
    headers = await _signup(api_client, "sums@example.com")
    await _reviewed(api_client, headers, tokens=1000, cost="0.50")
    await _reviewed(api_client, headers, tokens=500, cost="0.25")
    # Queued, not finished: not in the ledger, so not counted.
    await api_client.post("/analysis", json=_review(), headers=headers)

    usage = (await api_client.get("/usage", headers=headers)).json()

    assert usage["spent_usd"] == pytest.approx(0.75)
    assert (usage["tokens"], usage["runs"]) == (1500, 2)
    assert usage["daily"][-1]["tokens"] == 1500
    assert usage["months"][-1]["runs"] == 2
    assert usage["by_repo"] == [
        {"repo_full_name": "acme/widgets", "cost_usd": 0.75, "tokens": 1500, "runs": 2}
    ]
    [model] = usage["by_model"]
    assert model["model"] != "unknown" and model["runs"] == 2


async def test_owners_set_the_budget_and_the_change_is_audited(api_client: AsyncClient) -> None:
    headers = await _signup(api_client, "owner@example.com")
    await _reviewed(api_client, headers, cost="0.50")

    response = await api_client.put(
        "/usage/budget",
        json={"monthly_budget_usd": "1.00", "token_cap": None, "warn_percent": 40},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"monthly_budget_usd": 1.0, "token_cap": None, "warn_percent": 40}

    usage = (await api_client.get("/usage", headers=headers)).json()
    assert usage["status"] == "warning" and usage["used_percent"] == 50.0

    async for session in app.dependency_overrides[get_db]():
        await bind_org(session, _org(headers))
        entry = await session.scalar(select(AuditLog).where(AuditLog.action == "budget.changed"))
        assert entry is not None
        assert entry.extra["after"]["monthly_budget_usd"] == 1.0
        assert entry.extra["before"]["monthly_budget_usd"] is None
        break


async def test_reviews_are_refused_once_the_spend_cap_is_reached(api_client: AsyncClient) -> None:
    headers = await _signup(api_client, "capped@example.com")
    failed_id = await _reviewed(api_client, headers, cost="0.50")
    await api_client.put("/usage/budget", json={"monthly_budget_usd": "0.50"}, headers=headers)
    enqueued = api_client.fake_redis_pool.enqueued  # type: ignore[attr-defined]
    enqueued.clear()

    refused = await api_client.post("/analysis", json=_review(2), headers=headers)

    assert refused.status_code == 402
    assert "monthly budget of $0.50" in refused.json()["detail"]
    assert (await api_client.get("/usage", headers=headers)).json()["status"] == "exceeded"

    # A retry spends money too.
    async for session in app.dependency_overrides[get_db]():
        await bind_org(session, _org(headers))
        run = await session.get(AnalysisRun, uuid.UUID(failed_id))
        assert run is not None
        run.status = AnalysisRunStatus.FAILED
        await session.commit()
        break
    retry = await api_client.post(f"/analysis/{failed_id}/retry", headers=headers)
    assert retry.status_code == 402
    assert enqueued == []

    # Raising the cap lets reviews through again.
    await api_client.put("/usage/budget", json={"monthly_budget_usd": "5"}, headers=headers)
    assert (await api_client.post("/analysis", json=_review(2), headers=headers)).status_code == 202


async def test_the_token_cap_covers_models_that_report_no_cost(api_client: AsyncClient) -> None:
    headers = await _signup(api_client, "local@example.com")
    await _reviewed(api_client, headers, tokens=1200, cost="0")
    await api_client.put("/usage/budget", json={"token_cap": 1000}, headers=headers)

    usage = (await api_client.get("/usage", headers=headers)).json()
    refused = await api_client.post("/analysis", json=_review(2), headers=headers)

    assert usage["status"] == "exceeded" and usage["used_percent"] == 120.0
    assert refused.status_code == 402
    assert "1,000 tokens" in refused.json()["detail"]


async def test_members_can_see_usage_but_not_change_the_budget(api_client: AsyncClient) -> None:
    headers = await _signup(api_client, "member@example.com")
    async for session in app.dependency_overrides[get_db]():
        await bind_org(session, _org(headers))
        user = await session.scalar(select(User).where(User.email == "member@example.com"))
        assert user is not None
        user.role = UserRole.MEMBER
        await session.commit()
        break

    assert (await api_client.get("/usage", headers=headers)).status_code == 200
    changed = await api_client.put(
        "/usage/budget", json={"monthly_budget_usd": "100"}, headers=headers
    )
    assert changed.status_code == 403


@pytest.mark.parametrize(
    "body",
    [{"monthly_budget_usd": "-1"}, {"token_cap": 0}, {"warn_percent": 0}, {"warn_percent": 101}],
)
async def test_nonsense_budgets_are_rejected(
    api_client: AsyncClient, body: dict[str, object]
) -> None:
    headers = await _signup(api_client, f"bad-{uuid.uuid4().hex[:8]}@example.com")
    assert (await api_client.put("/usage/budget", json=body, headers=headers)).status_code == 422


async def test_usage_is_per_organization(api_client: AsyncClient) -> None:
    spender = await _signup(api_client, "spender@example.com")
    await _reviewed(api_client, spender, cost="3.00")
    await api_client.put("/usage/budget", json={"monthly_budget_usd": "1"}, headers=spender)
    other = await _signup(api_client, "other@example.com")

    usage = (await api_client.get("/usage", headers=other)).json()

    assert (usage["spent_usd"], usage["runs"], usage["by_repo"]) == (0, 0, [])
    assert (await api_client.post("/analysis", json=_review(), headers=other)).status_code == 202


def test_the_month_rolls_over_in_december() -> None:
    start, nxt = period_bounds(datetime(2026, 12, 31, 23, 59, tzinfo=UTC))
    assert (start, nxt) == (datetime(2026, 12, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))


def _state(spent: str, tokens: int, budget: str | None, cap: int | None) -> BudgetState:
    start, nxt = period_bounds()
    return BudgetState(
        period_start=start,
        resets_at=nxt,
        spent_usd=Decimal(spent),
        tokens=tokens,
        runs=1,
        budget_usd=Decimal(budget) if budget is not None else None,
        token_cap=cap,
        warn_percent=80,
    )


def test_the_closer_cap_decides_the_status() -> None:
    assert _state("1", 10, None, None).status is BudgetStatus.OK
    assert _state("7.9", 10, "10", None).status is BudgetStatus.OK
    assert _state("8", 10, "10", None).status is BudgetStatus.WARNING
    assert _state("1", 900, "10", 1000).status is BudgetStatus.WARNING
    assert _state("10", 0, "10", 1000).status is BudgetStatus.EXCEEDED
    # A zero budget means "no paid reviews at all".
    assert _state("0", 0, "0", None).status is BudgetStatus.EXCEEDED
