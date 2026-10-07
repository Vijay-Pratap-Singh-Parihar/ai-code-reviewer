"""A small async GitHub REST client covering only what revu needs.

Every method that reads repository data takes an *installation* token
explicitly rather than holding one, so a caller can never accidentally use
one installation's token against another installation's repositories.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

import httpx

from ghapp.auth import make_app_jwt

_API_VERSION = "2022-11-28"
_MAX_PAGES = 50


@dataclass(frozen=True)
class GitHubAppConfig:
    app_id: str
    private_key_pem: str
    client_id: str = ""
    client_secret: str = ""
    api_url: str = "https://api.github.com"
    web_url: str = "https://github.com"


class GitHubError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"GitHub API error {status_code}: {message}")
        self.status_code = status_code


class GitHubClient:
    def __init__(
        self, config: GitHubAppConfig, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._config = config
        self._http = httpx.AsyncClient(
            base_url=config.api_url,
            transport=transport,
            timeout=30.0,
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": _API_VERSION},
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._http.aclose()

    # --- auth helpers -------------------------------------------------

    def _app_headers(self) -> dict[str, str]:
        token = make_app_jwt(self._config.app_id, self._config.private_key_pem)
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _token_headers(token: str) -> dict[str, str]:
        return {"Authorization": f"token {token}"}

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            message = str(response.json().get("message", response.text))
        except ValueError:
            message = response.text
        raise GitHubError(response.status_code, message)

    async def _get_json(self, url: str, headers: dict[str, str], **params: Any) -> Any:
        response = await self._http.get(url, headers=headers, params=params or None)
        self._raise_for_status(response)
        return response.json()

    async def _paginate(
        self, url: str, headers: dict[str, str], *, key: str | None = None, **params: Any
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url: str | None = url
        query: dict[str, Any] | None = {"per_page": 100, **params}
        for _ in range(_MAX_PAGES):
            if next_url is None:
                break
            response = await self._http.get(next_url, headers=headers, params=query)
            self._raise_for_status(response)
            body = response.json()
            items.extend(body[key] if key else body)
            next_url = response.links.get("next", {}).get("url")
            query = None  # the "next" link already carries the query string
        return items

    # --- App-level (App JWT) ------------------------------------------

    async def get_installation(self, installation_id: int) -> dict[str, Any]:
        result: dict[str, Any] = await self._get_json(
            f"/app/installations/{installation_id}", self._app_headers()
        )
        return result

    async def create_installation_token(self, installation_id: int) -> str:
        response = await self._http.post(
            f"/app/installations/{installation_id}/access_tokens", headers=self._app_headers()
        )
        self._raise_for_status(response)
        return str(response.json()["token"])

    # --- installation-scoped (installation token) ----------------------

    async def list_installation_repositories(self, token: str) -> list[dict[str, Any]]:
        return await self._paginate(
            "/installation/repositories", self._token_headers(token), key="repositories"
        )

    async def list_open_pulls(self, token: str, full_name: str) -> list[dict[str, Any]]:
        return await self._paginate(
            f"/repos/{full_name}/pulls", self._token_headers(token), state="open"
        )

    async def get_pull(self, token: str, full_name: str, number: int) -> dict[str, Any]:
        result: dict[str, Any] = await self._get_json(
            f"/repos/{full_name}/pulls/{number}", self._token_headers(token)
        )
        return result

    async def get_pull_diff(self, token: str, full_name: str, number: int) -> str:
        headers = {**self._token_headers(token), "Accept": "application/vnd.github.diff"}
        response = await self._http.get(f"/repos/{full_name}/pulls/{number}", headers=headers)
        self._raise_for_status(response)
        return response.text

    # --- user-scoped (OAuth user token) --------------------------------

    async def exchange_oauth_code(self, code: str) -> str:
        """Exchange the `code` GitHub appends to the App's setup URL (when
        "Request user authorization (OAuth) during installation" is on) for a
        user-to-server token.
        """
        response = await self._http.post(
            f"{self._config.web_url}/login/oauth/access_token",
            headers={"Accept": "application/json"},
            json={
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "code": code,
            },
        )
        self._raise_for_status(response)
        body = response.json()
        if "access_token" not in body:
            raise GitHubError(400, str(body.get("error_description", "OAuth code exchange failed")))
        return str(body["access_token"])

    async def list_user_installation_ids(self, user_token: str) -> set[int]:
        installations = await self._paginate(
            "/user/installations", self._token_headers(user_token), key="installations"
        )
        return {int(item["id"]) for item in installations}


# --- calls made before any App exists (no credentials) -------------------------


async def convert_manifest_code(
    code: str,
    *,
    api_url: str = "https://api.github.com",
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Exchange the one-time code from the manifest flow's redirect for the
    new App's full configuration, including `id`, `slug`, `client_id`,
    `client_secret`, `webhook_secret` and `pem`. Valid once, for one hour."""
    async with httpx.AsyncClient(
        base_url=api_url,
        transport=transport,
        timeout=30.0,
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": _API_VERSION},
    ) as http:
        response = await http.post(f"/app-manifests/{code}/conversions")
    GitHubClient._raise_for_status(response)
    result: dict[str, Any] = response.json()
    return result


async def create_smee_channel(
    *, smee_url: str = "https://smee.io", transport: httpx.AsyncBaseTransport | None = None
) -> str:
    """Create a webhook relay channel; smee.io answers `/new` with a redirect
    to the fresh channel URL."""
    async with httpx.AsyncClient(transport=transport, timeout=15.0) as http:
        response = await http.get(f"{smee_url.rstrip('/')}/new", follow_redirects=False)
    location = str(response.headers.get("location", ""))
    if not response.is_redirect or not location.startswith(smee_url.rstrip("/") + "/"):
        raise GitHubError(response.status_code, "smee.io did not return a channel URL")
    return location
