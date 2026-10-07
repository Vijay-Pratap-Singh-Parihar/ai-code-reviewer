import json

import httpx
import pytest
from ghapp import (
    CredentialDecryptionError,
    GitHubError,
    SecretBox,
    convert_manifest_code,
    create_smee_channel,
    is_placeholder_secret,
    sign_payload,
    verify_signature,
)
from ghapp.manifest import build_manifest, creation_url
from ghapp.relay import parse_sse, to_webhook_delivery

# --- crypto -------------------------------------------------------------------


def test_secret_box_round_trip_and_ciphertext_is_not_plaintext() -> None:
    box = SecretBox("a-long-random-secret")
    token = box.encrypt("-----BEGIN PRIVATE KEY-----\nabc")
    assert "PRIVATE KEY" not in token
    assert box.decrypt(token) == "-----BEGIN PRIVATE KEY-----\nabc"


def test_secret_box_rejects_a_different_key() -> None:
    token = SecretBox("key-one").encrypt("s3cret")
    with pytest.raises(CredentialDecryptionError):
        SecretBox("key-two").decrypt(token)


def test_secret_box_accepts_a_real_fernet_key() -> None:
    from cryptography.fernet import Fernet

    box = SecretBox(Fernet.generate_key().decode())
    assert box.decrypt(box.encrypt("x")) == "x"


def test_placeholder_detection() -> None:
    assert is_placeholder_secret("change-me-dev-only-not-for-production")
    assert is_placeholder_secret("")
    assert not is_placeholder_secret("Zx9...real")


# --- manifest -----------------------------------------------------------------


def test_manifest_is_read_only_and_points_back_at_the_web_app() -> None:
    manifest = build_manifest(
        web_app_url="http://localhost:3000/", webhook_url="https://smee.io/abc", name="revu-x"
    )
    assert manifest["name"] == "revu-x"
    assert manifest["redirect_url"] == "http://localhost:3000/github/app-created"
    assert manifest["callback_urls"] == ["http://localhost:3000/github/setup"]
    assert manifest["request_oauth_on_install"] is True
    assert manifest["hook_attributes"] == {"url": "https://smee.io/abc", "active": True}
    assert set(manifest["default_permissions"].values()) == {"read"}
    assert manifest["default_events"] == ["pull_request", "push"]
    assert manifest["public"] is False


def test_default_name_is_unique_per_call() -> None:
    names = {build_manifest(web_app_url="x", webhook_url="y")["name"] for _ in range(5)}
    assert len(names) == 5 and all(n.startswith("revu-") for n in names)


def test_creation_url_for_user_and_org() -> None:
    assert (
        creation_url(web_url="https://github.com", state="s")
        == "https://github.com/settings/apps/new?state=s"
    )
    assert (
        creation_url(web_url="https://github.com", state="s", organization="acme-inc")
        == "https://github.com/organizations/acme-inc/settings/apps/new?state=s"
    )
    with pytest.raises(ValueError):
        creation_url(web_url="https://github.com", state="s", organization="../evil")


# --- relay ----------------------------------------------------------------------


def test_parse_sse_handles_events_comments_and_multiline_data() -> None:
    lines = [": keep-alive", "event: ready", "data: {}", "", "data: a", "data: b", "", ""]
    events = list(parse_sse(lines))
    assert [(e.event, e.data) for e in events] == [("ready", "{}"), ("message", "a\nb")]


def test_relayed_body_still_verifies_against_githubs_signature() -> None:
    # GitHub signs the compact JSON it sends; smee re-broadcasts the parsed
    # body, so the relay must re-serialize it byte-identically.
    original = {"action": "opened", "pull_request": {"title": "Ünïcode ✓", "number": 7}}
    raw = json.dumps(original, separators=(",", ":"), ensure_ascii=False).encode()
    signature = sign_payload("hook-secret", raw)
    smee_event = json.dumps(
        {
            "x-github-event": "pull_request",
            "x-github-delivery": "d-1",
            "x-hub-signature-256": signature,
            "host": "smee.io",
            "body": original,
            "query": {},
            "timestamp": 1,
        }
    )

    delivery = to_webhook_delivery(smee_event)

    assert delivery is not None
    assert verify_signature("hook-secret", delivery.body, delivery.headers["x-hub-signature-256"])
    assert delivery.headers["x-github-event"] == "pull_request"
    assert "host" not in delivery.headers  # only GitHub's own headers are replayed


@pytest.mark.parametrize("data", ["not json", "{}", '{"body": {}}', "[]"])
def test_non_delivery_events_are_skipped(data: str) -> None:
    assert to_webhook_delivery(data) is None


# --- unauthenticated calls -----------------------------------------------------------


async def test_convert_manifest_code_posts_to_the_conversion_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/app-manifests/one-time/conversions"
        assert "Authorization" not in request.headers
        return httpx.Response(201, json={"id": 9, "slug": "revu-x", "pem": "PEM"})

    result = await convert_manifest_code("one-time", transport=httpx.MockTransport(handler))
    assert result["slug"] == "revu-x"


async def test_convert_manifest_code_surfaces_an_expired_code() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(404, json={"message": "Not Found"}))
    with pytest.raises(GitHubError) as excinfo:
        await convert_manifest_code("used", transport=transport)
    assert excinfo.value.status_code == 404


async def test_create_smee_channel_reads_the_redirect() -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(307, headers={"location": "https://smee.io/AbC123"})
    )
    assert await create_smee_channel(transport=transport) == "https://smee.io/AbC123"


async def test_create_smee_channel_rejects_an_unexpected_answer() -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(307, headers={"location": "https://evil.example/x"})
    )
    with pytest.raises(GitHubError):
        await create_smee_channel(transport=transport)
