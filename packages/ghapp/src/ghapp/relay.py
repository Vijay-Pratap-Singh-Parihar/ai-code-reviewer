"""Pure helpers for relaying smee.io events to the webhook endpoint.

smee.io accepts GitHub's POST and re-broadcasts it over Server-Sent Events
as JSON: the original request headers as top-level keys plus the parsed
`body`. GitHub signs the compact JSON it sends, so the body is re-serialized
the same way the official smee-client does (`JSON.stringify`: no spaces,
non-ASCII kept as-is) for the HMAC check on the receiving side to pass.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

FORWARDED_HEADERS = (
    "x-github-event",
    "x-github-delivery",
    "x-github-hook-id",
    "x-github-hook-installation-target-id",
    "x-github-hook-installation-target-type",
    "x-hub-signature-256",
    "user-agent",
)


@dataclass(frozen=True)
class SseEvent:
    event: str
    data: str


class SseParser:
    """Incremental text/event-stream parser: feed it one line at a time."""

    def __init__(self) -> None:
        self._event = "message"
        self._data: list[str] = []

    def feed(self, raw: str) -> SseEvent | None:
        line = raw.rstrip("\r\n")
        if line == "":
            event = SseEvent(self._event, "\n".join(self._data)) if self._data else None
            self._event, self._data = "message", []
            return event
        if line.startswith(":"):
            return None  # comment / keep-alive
        if line.startswith("event:"):
            self._event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            self._data.append(line[len("data:") :].lstrip(" "))
        return None


def parse_sse(lines: Iterable[str]) -> Iterator[SseEvent]:
    parser = SseParser()
    for line in lines:
        event = parser.feed(line)
        if event is not None:
            yield event


@dataclass(frozen=True)
class WebhookDelivery:
    headers: dict[str, str]
    body: bytes


def to_webhook_delivery(data: str) -> WebhookDelivery | None:
    """Turn one smee event payload into the request to replay, or None for
    smee's own housekeeping events (`ready`, `ping`) and anything that
    isn't a GitHub delivery."""
    try:
        payload: Any = json.loads(data)
    except ValueError:
        return None
    if not isinstance(payload, dict) or "x-github-event" not in payload or "body" not in payload:
        return None
    headers = {k: str(payload[k]) for k in FORWARDED_HEADERS if k in payload}
    headers["content-type"] = "application/json"
    body = json.dumps(payload["body"], separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return WebhookDelivery(headers=headers, body=body)
