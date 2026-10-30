from httpx import AsyncClient


async def _signup(client: AsyncClient, email: str = "owner@example.com") -> dict:
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
    return response.json()


async def test_signup_returns_access_token_and_sets_refresh_cookie(api_client: AsyncClient) -> None:
    body = await _signup(api_client)

    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == "owner@example.com"
    assert body["user"]["role"] == "owner"
    assert "refresh_token" in api_client.cookies


async def test_signup_with_duplicate_email_is_rejected(api_client: AsyncClient) -> None:
    await _signup(api_client, email="dupe@example.com")

    response = await api_client.post(
        "/auth/signup",
        json={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "org_name": "Other Org",
            "email": "dupe@example.com",
            "password": "another-password",
        },
    )
    assert response.status_code == 409


async def test_login_with_correct_password_succeeds(api_client: AsyncClient) -> None:
    await _signup(api_client, email="login@example.com")
    api_client.cookies.clear()

    response = await api_client.post(
        "/auth/login", json={"email": "login@example.com", "password": "correct-horse-battery"}
    )
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "login@example.com"


async def test_login_with_wrong_password_is_rejected(api_client: AsyncClient) -> None:
    await _signup(api_client, email="wrongpw@example.com")

    response = await api_client.post(
        "/auth/login", json={"email": "wrongpw@example.com", "password": "not-the-password"}
    )
    assert response.status_code == 401


async def test_me_requires_bearer_token(api_client: AsyncClient) -> None:
    response = await api_client.get("/auth/me")
    assert response.status_code == 401


async def test_me_returns_current_user_with_valid_token(api_client: AsyncClient) -> None:
    body = await _signup(api_client, email="me@example.com")
    access_token = body["access_token"]

    response = await api_client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert response.status_code == 200
    assert response.json()["email"] == "me@example.com"


async def test_refresh_rotates_token_and_old_cookie_no_longer_works(
    api_client: AsyncClient,
) -> None:
    await _signup(api_client, email="rotate@example.com")
    old_refresh_token = api_client.cookies["refresh_token"]

    first_refresh = await api_client.post("/auth/refresh")
    assert first_refresh.status_code == 200
    new_refresh_token = api_client.cookies["refresh_token"]
    assert new_refresh_token != old_refresh_token

    # Reusing the now-rotated-away token must fail, not silently succeed.
    api_client.cookies.set("refresh_token", old_refresh_token)
    reuse_attempt = await api_client.post("/auth/refresh")
    assert reuse_attempt.status_code == 401


async def test_refresh_reuse_revokes_the_new_token_too(api_client: AsyncClient) -> None:
    """Reuse detection must revoke the whole chain, not just flag the old
    token — otherwise the legitimate holder of the newest token would be
    unaffected by a theft that already happened.
    """
    await _signup(api_client, email="reuse-chain@example.com")
    old_refresh_token = api_client.cookies["refresh_token"]

    await api_client.post("/auth/refresh")
    new_refresh_token = api_client.cookies["refresh_token"]

    api_client.cookies.set("refresh_token", old_refresh_token)
    reuse_attempt = await api_client.post("/auth/refresh")
    assert reuse_attempt.status_code == 401

    api_client.cookies.set("refresh_token", new_refresh_token)
    should_also_fail = await api_client.post("/auth/refresh")
    assert should_also_fail.status_code == 401


async def test_logout_revokes_refresh_token(api_client: AsyncClient) -> None:
    await _signup(api_client, email="logout@example.com")

    logout_response = await api_client.post("/auth/logout")
    assert logout_response.status_code == 204

    refresh_after_logout = await api_client.post("/auth/refresh")
    assert refresh_after_logout.status_code == 401


async def test_signup_needs_a_first_and_last_name_and_returns_them(
    api_client: AsyncClient,
) -> None:
    for missing in ("first_name", "last_name"):
        body = {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "org_name": "Acme Inc",
            "email": f"no-{missing}@example.com",
            "password": "correct-horse-battery",
        }
        body[missing] = "   "
        response = await api_client.post("/auth/signup", json=body)
        assert response.status_code == 422, missing

    user = (await _signup(api_client, email="named@example.com"))["user"]
    assert (user["first_name"], user["last_name"]) == ("Ada", "Lovelace")


async def test_a_user_can_change_their_own_name(api_client: AsyncClient) -> None:
    token = (await _signup(api_client, email="rename@example.com"))["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = await api_client.patch(
        "/auth/me", headers=headers, json={"first_name": " Grace ", "last_name": "Hopper"}
    )
    assert response.status_code == 200, response.text
    assert (response.json()["first_name"], response.json()["last_name"]) == ("Grace", "Hopper")

    me = (await api_client.get("/auth/me", headers=headers)).json()
    assert (me["first_name"], me["last_name"]) == ("Grace", "Hopper")

    blank = await api_client.patch(
        "/auth/me", headers=headers, json={"first_name": "", "last_name": "Hopper"}
    )
    assert blank.status_code == 422
