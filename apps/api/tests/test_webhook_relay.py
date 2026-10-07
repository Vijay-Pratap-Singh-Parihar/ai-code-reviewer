"""The smee → API relay, driven against a scripted SSE stream."""

import json

import httpx
from api.webhook_relay import relay_channel
from ghapp import sign_payload, verify_signature

CHANNEL = "https://smee.io/TestChannel123"
TARGET = "http://api:8000/github/webhook"


def _sse(*events: tuple[str, object]) -> bytes:
    chunks = [": keep-alive\n\n"]
    for name, data in events:
        chunks.append(f"event: {name}\ndata: {json.dumps(data)}\n\n")
    return "".join(chunks).encode()


async def test_relay_replays_github_deliveries_with_their_signature() -> None:
    body = {"action": "opened", "number": 7}
    raw = json.dumps(body, separators=(",", ":")).encode()
    signature = sign_payload("hook-secret", raw)
    stream = _sse(
        ("ready", {}),
        ("ping", {}),
        (
            "message",
            {
                "x-github-event": "pull_request",
                "x-github-delivery": "d-42",
                "x-hub-signature-256": signature,
                "body": body,
            },
        ),
    )
    forwarded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            assert str(request.url) == CHANNEL
            assert request.headers["Accept"] == "text/event-stream"
            return httpx.Response(
                200, content=stream, headers={"content-type": "text/event-stream"}
            )
        forwarded.append(request)
        return httpx.Response(200, json={"status": "processed"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        count = await relay_channel(http, CHANNEL, TARGET)

    assert count == 1  # smee's own ready/ping events are not forwarded
    request = forwarded[0]
    assert str(request.url) == TARGET
    assert request.headers["x-github-event"] == "pull_request"
    assert request.headers["x-github-delivery"] == "d-42"
    assert verify_signature("hook-secret", request.content, request.headers["x-hub-signature-256"])
