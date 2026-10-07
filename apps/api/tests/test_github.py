"""Stage 10: GitHub App linking, webhooks, and the connected-repo endpoints.

GitHub's REST API is replaced by an `httpx.MockTransport` (`FakeGitHub`)
injected through the `get_github_client` dependency; webhooks are signed
with a real HMAC exactly as GitHub signs them. Nothing calls an LLM.
"""

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from api.core.config import get_settings
from api.core.github import get_github_client
from api.main import app
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from ghapp import GitHubAppConfig, GitHubClient, sign_payload
from httpx import AsyncClient

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
    assert _enqueued(api_client) == [("review_github_pr", (run_id,))]

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
    assert repos and not any(r["is_active"] or r["connected"] for r in repos)
    assert (await api_client.get("/github/installations", headers=headers)).json() == []


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
    assert _enqueued(api_client)[1][1][1:] == ("c" * 40, False)


# --- connected repo endpoints -------------------------------------------------


async def test_list_pulls_merges_latest_run(
    api_client: AsyncClient, fake_github: FakeGitHub
) -> None:
    headers = await _signup(api_client, "pulls@example.com")
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
    assert _enqueued(api_client) == [("review_github_pr", (review.json()["id"],))]


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
    assert _enqueued(api_client) == [("sync_and_index_branch", (response.json()["id"], None, True))]


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
