"""Stage 10: GitHub App linking, webhooks, and the connected-repo endpoints.

GitHub's REST API is replaced by an `httpx.MockTransport` (`FakeGitHub`)
injected through the `get_github_client` dependency; webhooks are signed
with a real HMAC exactly as GitHub signs them. Nothing calls an LLM.
"""

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from api.core.config import get_settings
from api.core.github import get_github_client
from api.core.security import decode_access_token
from api.db.session import get_db
from api.main import app
from api.services import github as github_service
from api.testing import configure_review_model
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from db.organization import GithubInstallation, User, UserRole
from db.repository import Repository
from db.tenancy import bind_org
from ghapp import GitHubAppConfig, GitHubClient, sign_payload
from httpx import AsyncClient
from sqlalchemy import select

WEBHOOK_SECRET = "test-webhook-secret"
INSTALLATION_ID = 4242
HEAD_SHA = "a" * 40


class FakeGitHub:
    def __init__(self) -> None:
        self.user_installations = [INSTALLATION_ID]
        self.repos = [
            {"id": 101, "full_name": "acme/widgets", "default_branch": "main"},
            {"id": 102, "full_name": "acme/gadgets", "default_branch": "trunk"},
        ]
        self.pulls = [_pull_payload(7)]
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        if path == "/login/oauth/access_token":
            if json.loads(request.content)["code"] != "good-code":
                return httpx.Response(200, json={"error": "bad_verification_code"})
            return httpx.Response(200, json={"access_token": "ghu_user"})
        if path == "/user/installations":
            return httpx.Response(
                200, json={"installations": [{"id": i} for i in self.user_installations]}
            )
        if path.startswith("/app/installations/") and path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "ghs_inst"})
        if path.startswith("/app/installations/"):
            return httpx.Response(
                200,
                json={
                    "id": INSTALLATION_ID,
                    "account": {"login": "acme", "type": "Organization"},
                    "permissions": {"pull_requests": "read", "contents": "read"},
                },
            )
        if path == "/installation/repositories":
            return httpx.Response(200, json={"repositories": self.repos})
        if path == "/repos/acme/widgets/pulls":
            return httpx.Response(200, json=self.pulls)
        if path.startswith("/repos/acme/widgets/pulls/"):
            number = int(path.rsplit("/", 1)[1])
            match = [p for p in self.pulls if p["number"] == number]
            if not match:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json=match[0])
        return httpx.Response(404, json={"message": f"unhandled {path}"})


def _pull_payload(number: int, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "number": number,
        "title": f"PR {number}",
        "body": "body",
        "user": {"login": "octocat"},
        "base": {"ref": "main"},
        "head": {"sha": HEAD_SHA},
        "state": "open",
        "draft": False,
        "created_at": "2026-10-01T10:00:00Z",
        "updated_at": "2026-10-02T10:00:00Z",
        "merged_at": None,
        "html_url": f"https://github.com/acme/widgets/pull/{number}",
    }
    payload.update(overrides)
    return payload


@pytest.fixture(scope="module")
def app_config() -> GitHubAppConfig:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return GitHubAppConfig(
        app_id="1", private_key_pem=pem, client_id="cid", client_secret="csecret"
    )


@pytest.fixture
def fake_github(app_config: GitHubAppConfig) -> Iterator[FakeGitHub]:
    fake = FakeGitHub()

    async def override() -> AsyncIterator[GitHubClient]:
        async with GitHubClient(app_config, transport=httpx.MockTransport(fake.handler)) as gh:
            yield gh

    app.dependency_overrides[get_github_client] = override
    yield fake
    app.dependency_overrides.pop(get_github_client, None)


@pytest.fixture
def webhook_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(get_settings(), "github_webhook_secret", WEBHOOK_SECRET)
    return WEBHOOK_SECRET


async def _signup(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/auth/signup",
        json={"org_name": "Acme Inc", "email": email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _link(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.post(
        "/github/installations",
        json={"installation_id": INSTALLATION_ID, "code": "good-code"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def _repo_id(client: AsyncClient, headers: dict[str, str], full_name: str) -> str:
    repos = (await client.get("/repos", headers=headers)).json()
    return str(next(r["id"] for r in repos if r["full_name"] == full_name))


async def _send_webhook(
    client: AsyncClient, event: str, payload: dict[str, Any], *, delivery: str | None = None
) -> httpx.Response:
    body = json.dumps(payload).encode()
    return await client.post(
        "/github/webhook",
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery or str(uuid.uuid4()),
            "X-Hub-Signature-256": sign_payload(WEBHOOK_SECRET, body),
            "Content-Type": "application/json",
        },
    )


def _pr_event(action: str, **pull_overrides: Any) -> dict[str, Any]:
    return {
        "action": action,
        "installation": {"id": INSTALLATION_ID},
        "repository": {"id": 101, "full_name": "acme/widgets", "default_branch": "main"},
        "pull_request": _pull_payload(7, **pull_overrides),
    }


def org_of(headers: dict[str, str]) -> str:
    """The organisation a signed-up user's token acts for."""
    return str(decode_access_token(headers["Authorization"].removeprefix("Bearer ")).org_id)


def _enqueued(client: AsyncClient) -> list[tuple[str, tuple[object, ...]]]:
    return client.fake_redis_pool.enqueued  # type: ignore[attr-defined,no-any-return]


# --- app info + linking -----------------------------------------------------


async def test_app_info_reports_unconfigured_by_default(api_client: AsyncClient) -> None:
    headers = await _signup(api_client, "info@example.com")
    body = (await api_client.get("/github/app", headers=headers)).json()
    assert body["configured"] is False
    assert body["install_url"] is None
    assert body["webhook_configured"] is False


async def test_endpoints_needing_github_return_503_when_unconfigured(
    api_client: AsyncClient,
) -> None:
    headers = await _signup(api_client, "unconf@example.com")
    response = await api_client.post(
        "/github/installations",
        json={"installation_id": INSTALLATION_ID, "code": "good-code"},
        headers=headers,
    )
    assert response.status_code == 503


async def test_link_installation_syncs_repos_with_auto_review_off(
    api_client: AsyncClient, fake_github: FakeGitHub
) -> None:
    headers = await _signup(api_client, "link@example.com")

    linked = await _link(api_client, headers)

    assert linked["account_login"] == "acme"
    assert linked["account_type"] == "Organization"
    assert linked["repository_count"] == 2
    repos = (await api_client.get("/repos", headers=headers)).json()
    assert {r["full_name"]: r["default_branch"] for r in repos} == {
        "acme/gadgets": "trunk",
        "acme/widgets": "main",
    }
    assert all(r["connected"] and not r["auto_review_enabled"] for r in repos)
    listed = (await api_client.get("/github/installations", headers=headers)).json()
    assert [i["installation_id"] for i in listed] == [INSTALLATION_ID]


async def test_link_rejects_installation_the_user_cannot_access(
    api_client: AsyncClient, fake_github: FakeGitHub
) -> None:
    headers = await _signup(api_client, "spoof@example.com")
    fake_github.user_installations = [999]  # the user can't see INSTALLATION_ID

    response = await api_client.post(
        "/github/installations",
        json={"installation_id": INSTALLATION_ID, "code": "good-code"},
        headers=headers,
    )

    assert response.status_code == 403
    assert (await api_client.get("/repos", headers=headers)).json() == []


async def test_link_rejects_bad_oauth_code(
    api_client: AsyncClient, fake_github: FakeGitHub
) -> None:
    headers = await _signup(api_client, "badcode@example.com")
    response = await api_client.post(
        "/github/installations",
        json={"installation_id": INSTALLATION_ID, "code": "stale"},
        headers=headers,
    )
    assert response.status_code == 502


async def test_link_rejects_installation_owned_by_another_org(
    api_client: AsyncClient, fake_github: FakeGitHub
) -> None:
    await _link(api_client, await _signup(api_client, "first@example.com"))
    other = await _signup(api_client, "second@example.com")

    response = await api_client.post(
        "/github/installations",
        json={"installation_id": INSTALLATION_ID, "code": "good-code"},
        headers=other,
    )

    assert response.status_code == 409


async def test_link_adopts_a_repo_created_by_the_manual_flow(
    api_client: AsyncClient, fake_github: FakeGitHub
) -> None:
    headers = await _signup(api_client, "adopt@example.com")
    await configure_review_model(api_client, headers)
    manual = await api_client.post(
        "/analysis",
        json={
            "repo_full_name": "acme/widgets",
            "head_sha": "abc1234",
            "pr_number": 1,
            "pr_title": "t",
            "diff": "--- a/x\n+++ b/x\n",
        },
        headers=headers,
    )
    assert manual.status_code == 202

    await _link(api_client, headers)

    widgets = [
        r
        for r in (await api_client.get("/repos", headers=headers)).json()
        if r["full_name"] == "acme/widgets"
    ]
    assert len(widgets) == 1 and widgets[0]["connected"]


# --- webhooks -----------------------------------------------------------------


async def test_webhook_rejects_bad_signature(api_client: AsyncClient, webhook_secret: str) -> None:
    response = await api_client.post(
        "/github/webhook",
        content=b"{}",
        headers={
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "d-1",
            "X-Hub-Signature-256": sign_payload("wrong-secret", b"{}"),
        },
    )
    assert response.status_code == 401


async def test_webhook_returns_503_without_a_secret(api_client: AsyncClient) -> None:
    response = await api_client.post(
        "/github/webhook",
        content=b"{}",
        headers={"X-GitHub-Event": "ping", "X-GitHub-Delivery": "d-2"},
    )
    assert response.status_code == 503


async def test_webhook_ping(api_client: AsyncClient, webhook_secret: str) -> None:
    response = await _send_webhook(api_client, "ping", {"zen": "Keep it simple."})
    assert response.status_code == 200
    assert response.json()["status"] == "processed"


async def test_pr_opened_does_not_review_when_auto_review_is_off(
    api_client: AsyncClient, fake_github: FakeGitHub, webhook_secret: str
) -> None:
    await _link(api_client, await _signup(api_client, "off@example.com"))

    response = await _send_webhook(api_client, "pull_request", _pr_event("opened"))

    assert response.status_code == 200
    assert "disabled" in response.json()["detail"]
    assert _enqueued(api_client) == []


async def test_pr_opened_reviews_once_when_auto_review_is_on(
    api_client: AsyncClient, fake_github: FakeGitHub, webhook_secret: str
) -> None:
    headers = await _signup(api_client, "on@example.com")
    await configure_review_model(api_client, headers)
    await _link(api_client, headers)
    repo_id = await _repo_id(api_client, headers, "acme/widgets")
    patched = await api_client.patch(
        f"/repos/{repo_id}", json={"auto_review_enabled": True}, headers=headers
    )
    assert patched.json()["auto_review_enabled"] is True

    first = await _send_webhook(api_client, "pull_request", _pr_event("opened"), delivery="d-a")
    redelivered = await _send_webhook(
        api_client, "pull_request", _pr_event("opened"), delivery="d-a"
    )
    reopened = await _send_webhook(api_client, "pull_request", _pr_event("reopened"))

    assert first.json()["status"] == "processed"
    run_id = first.json()["enqueued"][0]
    assert redelivered.json()["status"] == "duplicate"
    assert "already reviewed" in reopened.json()["detail"]
    assert _enqueued(api_client) == [("review_github_pr", (run_id, org_of(headers)))]

    run = (await api_client.get(f"/analysis/{run_id}", headers=headers)).json()
    assert run["status"] == "queued"
    assert run["agent"] == "diff_only"
    assert run["pr_number"] == 7
    assert run["repo_full_name"] == "acme/widgets"
    assert run["head_sha"] == HEAD_SHA

    # A new push to the PR is new code: reviewed again.
    pushed = await _send_webhook(
        api_client, "pull_request", _pr_event("synchronize", head={"sha": "b" * 40})
    )
    assert len(pushed.json()["enqueued"]) == 1


async def test_draft_prs_are_not_auto_reviewed(
    api_client: AsyncClient, fake_github: FakeGitHub, webhook_secret: str
) -> None:
    headers = await _signup(api_client, "draft@example.com")
    await _link(api_client, headers)
    repo_id = await _repo_id(api_client, headers, "acme/widgets")
    await api_client.patch(f"/repos/{repo_id}", json={"auto_review_enabled": True}, headers=headers)

    response = await _send_webhook(api_client, "pull_request", _pr_event("opened", draft=True))

    assert "draft" in response.json()["detail"]
    assert _enqueued(api_client) == []


async def test_pr_closed_updates_state(
    api_client: AsyncClient, fake_github: FakeGitHub, webhook_secret: str
) -> None:
    await _link(api_client, await _signup(api_client, "closed@example.com"))

    response = await _send_webhook(
        api_client,
        "pull_request",
        _pr_event("closed", state="closed", merged_at="2026-10-03T10:00:00Z"),
    )

    assert response.json() == {
        "status": "processed",
        "detail": "pull request closed",
        "enqueued": [],
    }


async def test_webhook_for_unlinked_installation_is_ignored(
    api_client: AsyncClient, webhook_secret: str
) -> None:
    response = await _send_webhook(api_client, "pull_request", _pr_event("opened"))
    assert response.json()["status"] == "ignored"


async def test_malformed_payload_is_400_and_not_recorded(
    api_client: AsyncClient, fake_github: FakeGitHub, webhook_secret: str
) -> None:
    await _link(api_client, await _signup(api_client, "malformed@example.com"))
    bad = _pr_event("opened")
    del bad["pull_request"]["head"]

    first = await _send_webhook(api_client, "pull_request", bad, delivery="d-bad")
    retry = await _send_webhook(api_client, "pull_request", _pr_event("opened"), delivery="d-bad")

    assert first.status_code == 400
    # The failed attempt rolled back its delivery record, so a redelivery runs.
    assert retry.json()["status"] == "processed"


async def test_installation_deleted_deactivates_repos(
    api_client: AsyncClient, fake_github: FakeGitHub, webhook_secret: str
) -> None:
    headers = await _signup(api_client, "deleted@example.com")
    await _link(api_client, headers)

    await _send_webhook(
        api_client, "installation", {"action": "deleted", "installation": {"id": INSTALLATION_ID}}
    )

    repos = (await api_client.get("/repos", headers=headers)).json()
    installations = (await api_client.get("/github/installations", headers=headers)).json()

    assert repos and not any(r["is_active"] or r["connected"] for r in repos)
    # Disconnected, not deleted: the data stays for the retention period.
    assert all(r["disconnected_at"] and r["purge_after"] for r in repos)
    assert [i["status"] for i in installations] == ["uninstalled"]
    assert installations[0]["disconnected_repository_count"] == len(repos)
    assert installations[0]["purge_after"] is not None


async def test_installation_repositories_added_and_removed(
    api_client: AsyncClient, fake_github: FakeGitHub, webhook_secret: str
) -> None:
    headers = await _signup(api_client, "instrepos@example.com")
    await _link(api_client, headers)

    await _send_webhook(
        api_client,
        "installation_repositories",
        {
            "action": "added",
            "installation": {"id": INSTALLATION_ID},
            "repositories_added": [{"id": 103, "full_name": "acme/new-thing"}],
            "repositories_removed": [{"id": 102, "full_name": "acme/gadgets"}],
        },
    )

    repos = {r["full_name"]: r for r in (await api_client.get("/repos", headers=headers)).json()}
    assert repos["acme/new-thing"]["is_active"] is True
    assert repos["acme/new-thing"]["auto_review_enabled"] is False
    assert repos["acme/gadgets"]["is_active"] is False


async def test_push_to_default_branch_refreshes_only_indexed_branches(
    api_client: AsyncClient, fake_github: FakeGitHub, webhook_secret: str
) -> None:
    headers = await _signup(api_client, "push@example.com")
    await _link(api_client, headers)
    push = {
        "ref": "refs/heads/main",
        "after": "c" * 40,
        "deleted": False,
        "installation": {"id": INSTALLATION_ID},
        "repository": {"id": 101, "full_name": "acme/widgets", "default_branch": "main"},
    }

    not_indexed = await _send_webhook(api_client, "push", push)
    assert "not indexed" in not_indexed.json()["detail"]

    repo_id = await _repo_id(api_client, headers, "acme/widgets")
    await api_client.post(f"/repos/{repo_id}/index", json={}, headers=headers)
    feature = await _send_webhook(api_client, "push", {**push, "ref": "refs/heads/feature"})
    indexed = await _send_webhook(api_client, "push", push)

    assert feature.json()["status"] == "ignored"
    assert indexed.json()["detail"] == "index refresh queued"
    assert [name for name, _ in _enqueued(api_client)] == [
        "sync_and_index_branch",
        "sync_and_index_branch",
    ]
    assert _enqueued(api_client)[1][1][1:] == (org_of(headers), "c" * 40, False)


# --- connected repo endpoints -------------------------------------------------


async def test_a_pr_review_needs_a_configured_review_model(
    api_client: AsyncClient, fake_github: FakeGitHub, webhook_secret: str
) -> None:
    headers = await _signup(api_client, "nomodel-pr@example.com")
    await _link(api_client, headers)
    repo_id = await _repo_id(api_client, headers, "acme/widgets")
    await api_client.patch(f"/repos/{repo_id}", json={"auto_review_enabled": True}, headers=headers)

    manual = await api_client.post(
        f"/repos/{repo_id}/pulls/7/analysis", json={"agent": "diff_only"}, headers=headers
    )
    automatic = await _send_webhook(api_client, "pull_request", _pr_event("opened"))

    assert manual.status_code == 409
    assert automatic.json()["detail"] == "no AI provider is configured for reviews"
    assert _enqueued(api_client) == []


async def test_list_pulls_merges_latest_run(
    api_client: AsyncClient, fake_github: FakeGitHub
) -> None:
    headers = await _signup(api_client, "pulls@example.com")
    await configure_review_model(api_client, headers)
    await _link(api_client, headers)
    repo_id = await _repo_id(api_client, headers, "acme/widgets")

    before = (await api_client.get(f"/repos/{repo_id}/pulls", headers=headers)).json()
    review = await api_client.post(
        f"/repos/{repo_id}/pulls/7/analysis", json={"agent": "diff_only"}, headers=headers
    )
    after = (await api_client.get(f"/repos/{repo_id}/pulls", headers=headers)).json()

    assert before[0]["number"] == 7 and before[0]["latest_run"] is None
    assert review.status_code == 202, review.text
    assert after[0]["latest_run"]["id"] == review.json()["id"]
    assert after[0]["latest_run"]["status"] == "queued"
    assert _enqueued(api_client) == [("review_github_pr", (review.json()["id"], org_of(headers)))]


async def test_review_unknown_pr_is_404(api_client: AsyncClient, fake_github: FakeGitHub) -> None:
    headers = await _signup(api_client, "nopr@example.com")
    await _link(api_client, headers)
    repo_id = await _repo_id(api_client, headers, "acme/widgets")

    response = await api_client.post(
        f"/repos/{repo_id}/pulls/999/analysis", json={}, headers=headers
    )

    assert response.status_code == 404


async def test_cross_file_review_requires_a_ready_index(
    api_client: AsyncClient, fake_github: FakeGitHub
) -> None:
    headers = await _signup(api_client, "xfile@example.com")
    await _link(api_client, headers)
    repo_id = await _repo_id(api_client, headers, "acme/widgets")

    response = await api_client.post(
        f"/repos/{repo_id}/pulls/7/analysis", json={"agent": "cross_file"}, headers=headers
    )

    assert response.status_code == 409
    assert _enqueued(api_client) == []


async def test_index_endpoint_defaults_to_the_default_branch(
    api_client: AsyncClient, fake_github: FakeGitHub
) -> None:
    headers = await _signup(api_client, "index@example.com")
    await _link(api_client, headers)
    repo_id = await _repo_id(api_client, headers, "acme/gadgets")

    response = await api_client.post(
        f"/repos/{repo_id}/index", json={"force_full": True}, headers=headers
    )

    assert response.status_code == 202
    assert response.json()["branch_name"] == "trunk"
    assert _enqueued(api_client) == [
        ("sync_and_index_branch", (response.json()["id"], org_of(headers), None, True))
    ]


async def test_repos_are_invisible_to_other_orgs(
    api_client: AsyncClient, fake_github: FakeGitHub
) -> None:
    headers = await _signup(api_client, "owner@example.com")
    await _link(api_client, headers)
    repo_id = await _repo_id(api_client, headers, "acme/widgets")
    stranger = await _signup(api_client, "stranger@example.com")

    assert (await api_client.get("/repos", headers=stranger)).json() == []
    for method, path, body in [
        ("PATCH", f"/repos/{repo_id}", {"auto_review_enabled": True}),
        ("GET", f"/repos/{repo_id}/pulls", None),
        ("POST", f"/repos/{repo_id}/pulls/7/analysis", {}),
        ("POST", f"/repos/{repo_id}/index", {}),
    ]:
        response = await api_client.request(method, path, json=body, headers=stranger)
        assert response.status_code == 404, (method, path)


async def test_manual_repo_cannot_use_github_endpoints(
    api_client: AsyncClient, fake_github: FakeGitHub
) -> None:
    headers = await _signup(api_client, "manualonly@example.com")
    await configure_review_model(api_client, headers)
    await api_client.post(
        "/analysis",
        json={
            "repo_full_name": "solo/project",
            "head_sha": "abc1234",
            "pr_number": 1,
            "pr_title": "t",
            "diff": "--- a/x\n+++ b/x\n",
        },
        headers=headers,
    )
    repo_id = await _repo_id(api_client, headers, "solo/project")

    pulls = await api_client.get(f"/repos/{repo_id}/pulls", headers=headers)
    index = await api_client.post(f"/repos/{repo_id}/index", json={}, headers=headers)

    assert pulls.status_code == 409
    assert index.status_code == 409


# --- connection lifecycle (isolation hardening, PR C) -------------------------


def _repos_by_name(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["full_name"]: r for r in rows}


async def _audit_actions(client: AsyncClient, headers: dict[str, str]) -> list[str]:
    page = (await client.get("/audit?limit=200", headers=headers)).json()
    return [e["action"] for e in page["entries"]]


def _repo_change(*repo_ids: int, action: str = "removed") -> dict[str, Any]:
    names = {101: "acme/widgets", 102: "acme/gadgets"}
    key = "repositories_removed" if action == "removed" else "repositories_added"
    return {
        "action": action,
        "installation": {"id": INSTALLATION_ID},
        key: [{"id": i, "full_name": names[i]} for i in repo_ids],
    }


async def test_a_removed_repository_is_disconnected_then_restored_when_added_back(
    api_client: AsyncClient, fake_github: FakeGitHub, webhook_secret: str
) -> None:
    headers = await _signup(api_client, "restore@example.com")
    await _link(api_client, headers)

    await _send_webhook(api_client, "installation_repositories", _repo_change(102))
    removed = _repos_by_name((await api_client.get("/repos", headers=headers)).json())
    await _send_webhook(api_client, "installation_repositories", _repo_change(102, action="added"))
    restored = _repos_by_name((await api_client.get("/repos", headers=headers)).json())

    gadgets = removed["acme/gadgets"]
    assert gadgets["is_active"] is False and gadgets["connected"] is False
    assert gadgets["disconnected_at"] and gadgets["purge_after"] > gadgets["disconnected_at"]
    assert restored["acme/gadgets"]["id"] == gadgets["id"]  # same row: history kept
    assert restored["acme/gadgets"]["is_active"] is True
    assert restored["acme/gadgets"]["disconnected_at"] is None
    actions = await _audit_actions(api_client, headers)
    assert actions[:2] == ["repository.connected", "repository.disconnected"]


async def test_unsuspending_does_not_revive_repositories_removed_meanwhile(
    api_client: AsyncClient, fake_github: FakeGitHub, webhook_secret: str
) -> None:
    headers = await _signup(api_client, "suspend@example.com")
    await _link(api_client, headers)
    installation = {"id": INSTALLATION_ID}

    await _send_webhook(
        api_client, "installation", {"action": "suspend", "installation": installation}
    )
    suspended = (await api_client.get("/github/installations", headers=headers)).json()
    await _send_webhook(api_client, "installation_repositories", _repo_change(102))
    await _send_webhook(
        api_client, "installation", {"action": "unsuspend", "installation": installation}
    )

    repos = _repos_by_name((await api_client.get("/repos", headers=headers)).json())
    after = (await api_client.get("/github/installations", headers=headers)).json()
    assert suspended[0]["status"] == "suspended"
    assert after[0]["status"] == "active"
    assert repos["acme/widgets"]["is_active"] is True
    assert repos["acme/gadgets"]["is_active"] is False
    assert repos["acme/gadgets"]["disconnected_at"] is not None


async def test_a_repository_stays_with_the_installation_that_connected_it_first(
    api_client: AsyncClient, fake_github: FakeGitHub
) -> None:
    """Two installations of one organisation that can both see a repository
    must not take turns owning it on every sync; the second only takes over
    once the first is uninstalled."""
    headers = await _signup(api_client, "pinned@example.com")
    await _link(api_client, headers)
    payload = [{"id": 101, "full_name": "acme/widgets", "default_branch": "main"}]

    async for session in app.dependency_overrides[get_db]():
        await bind_org(session, uuid.UUID(org_of(headers)))
        first = await session.scalar(select(GithubInstallation))
        assert first is not None
        second = GithubInstallation(
            org_id=first.org_id,
            installation_id=INSTALLATION_ID + 1,
            account_login="acme-second",
            installed_at=datetime.now(UTC),
        )
        session.add(second)
        await session.flush()

        kept = await github_service.upsert_repositories(session, second, payload)
        repo = await session.scalar(select(Repository).where(Repository.github_repo_id == 101))
        assert kept == [] and repo is not None and repo.installation_id == first.id

        first.uninstalled_at = datetime.now(UTC)
        moved = await github_service.upsert_repositories(session, second, payload)
        assert [r.id for r in moved] == [repo.id]
        assert repo.installation_id == second.id
        break


async def test_repository_data_can_be_deleted_only_once_disconnected(
    api_client: AsyncClient, fake_github: FakeGitHub, webhook_secret: str
) -> None:
    headers = await _signup(api_client, "delete-repo@example.com")
    await _link(api_client, headers)
    me = (await api_client.get("/auth/me", headers=headers)).json()
    repo_id = await _repo_id(api_client, headers, "acme/gadgets")

    while_connected = await api_client.delete(f"/repos/{repo_id}/data", headers=headers)
    await _send_webhook(api_client, "installation_repositories", _repo_change(102))
    once_removed = await api_client.delete(f"/repos/{repo_id}/data", headers=headers)

    assert while_connected.status_code == 409
    assert once_removed.status_code == 202
    assert _enqueued(api_client) == [
        ("purge_repository_data", (org_of(headers), repo_id, me["id"]))
    ]
    assert "data.deletion_requested" in await _audit_actions(api_client, headers)


async def test_connection_data_can_be_deleted_only_after_uninstalling(
    api_client: AsyncClient, fake_github: FakeGitHub, webhook_secret: str
) -> None:
    headers = await _signup(api_client, "delete-conn@example.com")
    linked = await _link(api_client, headers)

    while_installed = await api_client.delete(
        f"/github/installations/{linked['id']}/data", headers=headers
    )
    await _send_webhook(
        api_client, "installation", {"action": "deleted", "installation": {"id": INSTALLATION_ID}}
    )
    after_uninstall = await api_client.delete(
        f"/github/installations/{linked['id']}/data", headers=headers
    )

    assert while_installed.status_code == 409
    assert after_uninstall.status_code == 202
    assert [name for name, _ in _enqueued(api_client)] == ["purge_installation_data"]
    actions = await _audit_actions(api_client, headers)
    assert "installation.uninstalled" in actions and "data.deletion_requested" in actions


async def test_settings_deletion_and_audit_are_for_org_admins_only(
    api_client: AsyncClient, fake_github: FakeGitHub
) -> None:
    headers = await _signup(api_client, "member-only@example.com")
    await _link(api_client, headers)
    repo_id = await _repo_id(api_client, headers, "acme/widgets")
    async for session in app.dependency_overrides[get_db]():
        user = await session.scalar(select(User).where(User.email == "member-only@example.com"))
        assert user is not None
        user.role = UserRole.MEMBER
        await session.commit()
        break

    patched = await api_client.patch(
        f"/repos/{repo_id}", json={"auto_review_enabled": True}, headers=headers
    )
    deleted = await api_client.delete(f"/repos/{repo_id}/data", headers=headers)
    audit = await api_client.get("/audit", headers=headers)

    assert (patched.status_code, deleted.status_code, audit.status_code) == (403, 403, 403)


async def test_another_organisation_can_neither_delete_nor_read_the_audit_trail(
    api_client: AsyncClient, fake_github: FakeGitHub
) -> None:
    owner = await _signup(api_client, "audit-owner@example.com")
    await _link(api_client, owner)
    repo_id = await _repo_id(api_client, owner, "acme/widgets")
    other = await _signup(api_client, "audit-other@example.com")

    deleted = await api_client.delete(f"/repos/{repo_id}/data", headers=other)
    other_audit = (await api_client.get("/audit", headers=other)).json()["entries"]

    assert deleted.status_code == 404
    assert other_audit == []


async def test_login_attempts_are_audited_and_the_trail_pages(api_client: AsyncClient) -> None:
    headers = await _signup(api_client, "logins@example.com")
    for password in ("wrong-password-1", "correct-horse-battery", "wrong-password-2"):
        await api_client.post(
            "/auth/login", json={"email": "logins@example.com", "password": password}
        )

    first = (await api_client.get("/audit?limit=2", headers=headers)).json()
    second = (
        await api_client.get(
            "/audit", params={"limit": 2, "before": first["next_before"]}, headers=headers
        )
    ).json()

    assert [e["action"] for e in first["entries"]] == ["auth.login_failed", "auth.login"]
    assert [e["action"] for e in second["entries"]] == ["auth.login_failed"]
    assert first["entries"][0]["actor_email"] == "logins@example.com"
    assert second["next_before"] is None
