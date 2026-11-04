"""Fields every log line carries while one request or job is being handled:
the request id, and the organisation, run or job it is about.

One id follows a review from the HTTP request (or GitHub delivery) that
started it, through the queue, into the worker job that ran it, so a single
search finds every line about it. Fields live in a context variable: each
request and each job sees only its own.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

REQUEST_ID_HEADER = "X-Request-ID"

# Never mutated: every change sets a new dict, so a copied context (a task,
# a thread) can't see another's fields.
_fields: ContextVar[dict[str, str]] = ContextVar("revu_log_fields", default={})  # noqa: B039

# What we accept from a caller's X-Request-ID; anything else is replaced, so
# the header can't inject text into log lines.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def new_request_id() -> str:
    return uuid.uuid4().hex


def clean_request_id(value: str | None) -> str | None:
    return value if value and _VALID_REQUEST_ID.match(value) else None


def current_fields() -> dict[str, str]:
    return _fields.get()


def request_id() -> str | None:
    return _fields.get().get("request_id")


def add_fields(**fields: object) -> None:
    """Add fields for the rest of the current request or job."""
    _fields.set({**_fields.get(), **_strings(fields)})


@contextmanager
def bound(**fields: object) -> Iterator[None]:
    """Fields for the duration of the block, restored afterwards."""
    token = _fields.set({**_fields.get(), **_strings(fields)})
    try:
        yield
    finally:
        _fields.reset(token)


def _strings(fields: dict[str, object]) -> dict[str, str]:
    return {key: str(value) for key, value in fields.items() if value is not None and value != ""}
