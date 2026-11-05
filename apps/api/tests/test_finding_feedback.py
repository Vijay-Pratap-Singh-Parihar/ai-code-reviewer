"""PUT /analysis/{run_id}/findings/{finding_id}/feedback."""

import uuid
from datetime import UTC, datetime

from api.core.security import decode_access_token
from api.db.session import get_db
from api.main import app
from api.testing import configure_review_model
from db.organization import User
from db.pull_request import AnalysisRun, AnalysisRunStatus, FindingRecord
from db.tenancy import bind_org
from httpx import AsyncClient
from revu.models import FindingCategory, Severity
from sqlalchemy import select


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


def _org(headers: dict[str, str]) -> uuid.UUID:
    return decode_access_token(headers["Authorization"].removeprefix("Bearer ")).org_id


async def _reviewed_run(client: AsyncClient, headers: dict[str, str]) -> str:
    """A finished review with two findings."""
    created = await client.post(
        "/analysis",
        json={
            "repo_full_name": "acme/widgets",
            "head_sha": "abc1234",
            "pr_number": 1,
            "pr_title": "t",
            "diff": "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n",
        },
        headers=headers,
    )
    run_id = created.json()["id"]
    async for session in app.dependency_overrides[get_db]():
        await bind_org(session, _org(headers))
        run = await session.get(AnalysisRun, uuid.UUID(run_id))
        assert run is not None
        run.status = AnalysisRunStatus.SUCCEEDED
        run.finished_at = datetime.now(UTC)
        for line in (1, 2):
            session.add(
                FindingRecord(
                    run_id=run.id,
                    org_id=run.org_id,
                    file_path="x.py",
                    line_start=line,
                    line_end=line,
                    category=FindingCategory.CORRECTNESS,
                    severity=Severity.HIGH,
                    message=f"finding {line}",
                    confidence=0.8,
                    agent_name="diff_only",
                )
            )
        await session.commit()
        break
    return str(run_id)


async def _findings(client: AsyncClient, headers: dict[str, str], run_id: str) -> list[dict]:  # type: ignore[type-arg]
    run = (await client.get(f"/analysis/{run_id}", headers=headers)).json()
    return sorted(run["findings"], key=lambda f: f["line_start"])


async def test_findings_can_be_accepted_dismissed_and_cleared(api_client: AsyncClient) -> None:
    headers = await _signup(api_client, "feedback@example.com")
    run_id = await _reviewed_run(api_client, headers)
    first, second = await _findings(api_client, headers, run_id)
    assert first["id"] and first["developer_action"] is None

    accepted = await api_client.put(
        f"/analysis/{run_id}/findings/{first['id']}/feedback",
        json={"action": "accepted", "reason": "incorrect"},
        headers=headers,
    )
    dismissed = await api_client.put(
        f"/analysis/{run_id}/findings/{second['id']}/feedback",
        json={"action": "dismissed", "reason": "incorrect"},
        headers=headers,
    )

    assert accepted.status_code == 200, accepted.text
    # A reason only means something for a dismissal.
    assert accepted.json()["developer_action"] == "accepted"
    assert accepted.json()["feedback_reason"] is None
    assert accepted.json()["feedback_at"] is not None
    assert dismissed.json()["feedback_reason"] == "incorrect"
    first, second = await _findings(api_client, headers, run_id)
    assert (first["developer_action"], second["developer_action"]) == ("accepted", "dismissed")

    stats = (await api_client.get("/history/stats", headers=headers)).json()
    assert (stats["accepted"], stats["dismissed"], stats["acceptance_rate"]) == (1, 1, 0.5)

    cleared = await api_client.put(
        f"/analysis/{run_id}/findings/{second['id']}/feedback",
        json={"action": None},
        headers=headers,
    )
    assert cleared.json()["developer_action"] is None
    assert cleared.json()["feedback_reason"] is None


async def test_feedback_records_who_gave_it(api_client: AsyncClient) -> None:
    headers = await _signup(api_client, "who@example.com")
    run_id = await _reviewed_run(api_client, headers)
    first, _ = await _findings(api_client, headers, run_id)
    await api_client.put(
        f"/analysis/{run_id}/findings/{first['id']}/feedback",
        json={"action": "dismissed"},
        headers=headers,
    )

    async for session in app.dependency_overrides[get_db]():
        await bind_org(session, _org(headers))
        finding = await session.get(FindingRecord, uuid.UUID(first["id"]))
        user = await session.scalar(select(User).where(User.email == "who@example.com"))
        assert finding is not None and user is not None
        assert finding.feedback_by == user.id
        assert finding.feedback_reason is None  # none given
        break


async def test_feedback_is_refused_for_other_organizations_and_bad_input(
    api_client: AsyncClient,
) -> None:
    owner = await _signup(api_client, "owner-fb@example.com")
    run_id = await _reviewed_run(api_client, owner)
    first, _ = await _findings(api_client, owner, run_id)
    other = await _signup(api_client, "other-fb@example.com")
    url = f"/analysis/{run_id}/findings/{first['id']}/feedback"

    assert (
        await api_client.put(url, json={"action": "accepted"}, headers=other)
    ).status_code == 404
    unknown = f"/analysis/{run_id}/findings/{uuid.uuid4()}/feedback"
    assert (
        await api_client.put(unknown, json={"action": "accepted"}, headers=owner)
    ).status_code == 404
    assert (await api_client.put(url, json={"action": "maybe"}, headers=owner)).status_code == 422
    assert (
        await api_client.put(url, json={"action": "dismissed", "reason": "meh"}, headers=owner)
    ).status_code == 422
