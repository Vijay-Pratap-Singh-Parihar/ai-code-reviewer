import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from ghapp import GitHubAppConfig, GitHubClient, GitHubError


@pytest.fixture(scope="module")
def config() -> GitHubAppConfig:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return GitHubAppConfig(
        app_id="1", private_key_pem=pem, client_id="cid", client_secret="csecret"
    )


def _client(config: GitHubAppConfig, handler) -> GitHubClient:  # type: ignore[no-untyped-def]
    return GitHubClient(config, transport=httpx.MockTransport(handler))


async def test_installation_token_uses_app_jwt(config: GitHubAppConfig) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"token": "ghs_abc"})

    async with _client(config, handler) as gh:
        assert await gh.create_installation_token(42) == "ghs_abc"

    assert seen[0].method == "POST"
    assert seen[0].url.path == "/app/installations/42/access_tokens"
    assert seen[0].headers["Authorization"].startswith("Bearer ey")


async def test_paginates_installation_repositories(config: GitHubAppConfig) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "token ghs_abc"
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json={"repositories": [{"id": 2}]})
        assert request.url.params["per_page"] == "100"
        nxt = "https://api.github.com/installation/repositories?per_page=100&page=2"
        return httpx.Response(
            200, json={"repositories": [{"id": 1}]}, headers={"Link": f'<{nxt}>; rel="next"'}
        )

    async with _client(config, handler) as gh:
        repos = await gh.list_installation_repositories("ghs_abc")

    assert [r["id"] for r in repos] == [1, 2]


async def test_pull_diff_requests_diff_media_type(config: GitHubAppConfig) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/widgets/pulls/7"
        assert request.headers["Accept"] == "application/vnd.github.diff"
        return httpx.Response(200, text="diff --git a/x b/x\n")

    async with _client(config, handler) as gh:
        assert (await gh.get_pull_diff("t", "acme/widgets", 7)).startswith("diff --git")


async def test_list_open_pulls_filters_state(config: GitHubAppConfig) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["state"] == "open"
        return httpx.Response(200, json=[{"number": 3}])

    async with _client(config, handler) as gh:
        assert await gh.list_open_pulls("t", "acme/widgets") == [{"number": 3}]


async def test_error_surfaces_status_and_message(config: GitHubAppConfig) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    async with _client(config, handler) as gh:
        with pytest.raises(GitHubError) as excinfo:
            await gh.get_pull("t", "acme/widgets", 1)

    assert excinfo.value.status_code == 404
    assert "Not Found" in str(excinfo.value)


async def test_oauth_exchange_and_user_installations(config: GitHubAppConfig) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            assert request.url.host == "github.com"
            body = json.loads(request.content)
            assert body == {"client_id": "cid", "client_secret": "csecret", "code": "c0de"}
            return httpx.Response(200, json={"access_token": "ghu_user"})
        assert request.url.path == "/user/installations"
        assert request.headers["Authorization"] == "token ghu_user"
        return httpx.Response(200, json={"installations": [{"id": 10}, {"id": 11}]})

    async with _client(config, handler) as gh:
        user_token = await gh.exchange_oauth_code("c0de")
        assert await gh.list_user_installation_ids(user_token) == {10, 11}


async def test_oauth_exchange_bad_code_raises(config: GitHubAppConfig) -> None:
    # GitHub answers a bad code with HTTP 200 and an error body.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"error": "bad_verification_code", "error_description": "expired"}
        )

    async with _client(config, handler) as gh:
        with pytest.raises(GitHubError, match="expired"):
            await gh.exchange_oauth_code("stale")
