"""Context fields, request ids, and what the log formatters write."""

import json
import logging
import sys

from observability import (
    add_fields,
    bound,
    clean_request_id,
    configure_logging,
    current_fields,
    redact,
)
from observability.logs import JsonFormatter, TextFormatter


def _record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("revu.test", logging.INFO, __file__, 1, message, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_fields_last_for_the_block_and_nest() -> None:
    with bound(request_id="r1", org_id="o1"):
        with bound(run_id="run-9", org_id=None):  # None leaves a field as it was
            assert current_fields() == {"request_id": "r1", "org_id": "o1", "run_id": "run-9"}
        assert current_fields() == {"request_id": "r1", "org_id": "o1"}
    assert current_fields() == {}


def test_added_fields_stay_until_the_enclosing_block_ends() -> None:
    with bound(request_id="r1"):
        add_fields(user_id="u1")
        assert current_fields()["user_id"] == "u1"
    assert current_fields() == {}


def test_a_callers_request_id_is_kept_only_when_it_is_plain() -> None:
    assert clean_request_id("abc-123_x.y:z") == "abc-123_x.y:z"
    assert clean_request_id("evil\nINFO forged line") is None
    assert clean_request_id("x" * 65) is None
    assert clean_request_id(None) is None


def test_json_lines_carry_context_extras_and_the_traceback() -> None:
    formatter = JsonFormatter("worker")
    try:
        raise ValueError("boom")
    except ValueError:
        record = _record("job %s failed", duration_ms=12)
        record.args = ("analyze_pr",)
        record.exc_info = sys.exc_info()

    with bound(request_id="r1", run_id="run-1"):
        entry = json.loads(formatter.format(record))

    assert entry["service"] == "worker"
    assert entry["message"] == "job analyze_pr failed"
    assert entry["request_id"] == "r1" and entry["run_id"] == "run-1"
    assert entry["duration_ms"] == 12
    assert "ValueError: boom" in entry["exception"]


def test_text_lines_end_with_the_context_fields() -> None:
    with bound(request_id="r1"):
        line = TextFormatter().format(_record("GET /repos 200", status=200))

    assert line.endswith("GET /repos 200  request_id=r1 status=200")


def test_credentials_are_masked_wherever_they_appear() -> None:
    groq = "gsk_" + "a1B2" * 10
    github = "ghs_" + "Z9y8" * 10
    message = (
        f"calling groq with {groq}; Authorization: Bearer {'t' * 40}; "
        f'payload {{"api_key": "{"k" * 20}"}}; token={github}'
    )

    masked = redact(message)

    assert groq not in masked and github not in masked and "t" * 40 not in masked
    assert "k" * 20 not in masked
    assert "gsk_***" in masked and "Bearer ***" in masked
    assert redact("a normal line about 12 findings") == "a normal line about 12 findings"


def test_a_secret_in_a_traceback_is_masked_too() -> None:
    try:
        raise RuntimeError("provider said: invalid key sk-ant-" + "q" * 30)
    except RuntimeError:
        record = _record("call failed")
        record.exc_info = sys.exc_info()

    entry = json.loads(JsonFormatter("api").format(record))
    assert "q" * 30 not in entry["exception"]


def test_configuring_twice_keeps_a_single_working_handler() -> None:
    """The worker configures logging at import and again at startup (arq
    applies its own config in between); the second call must not close the
    stream the first one opened."""
    root = logging.getLogger()
    before, level = root.handlers[:], root.level
    try:
        configure_logging(service="worker", fmt="json")
        configure_logging(service="worker", fmt="json")

        logging.getLogger("revu.test").warning("still writing → ok")

        assert len(root.handlers) == 1
        handler = root.handlers[0]
        assert isinstance(handler, logging.StreamHandler) and not handler.stream.closed
    finally:
        root.handlers, root.level = before, level
