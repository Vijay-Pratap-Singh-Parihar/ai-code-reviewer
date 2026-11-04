"""Log configuration for the API and the worker.

`json` (the production default) writes one JSON object per line for a log
pipeline (Loki, CloudWatch, Datadog) to index: timestamp, level, service,
logger, message, the context fields (`observability.context`), any `extra`
fields, and the traceback. `text` is the same information for a terminal.

Credentials never reach a log: provider keys, GitHub tokens and bearer
tokens are masked in every message and traceback, whatever logged them.
"""

from __future__ import annotations

import io
import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Literal, TextIO

from observability.context import current_fields

LogFormat = Literal["json", "text"]

# Attributes every LogRecord has; anything else on a record came from
# `extra=` and is written as a field.
_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",  # uvicorn's ANSI-coloured copy of the message
}

# Provider keys (OpenAI/Anthropic/Groq/xAI), GitHub tokens, bearer tokens,
# and key=value / "key": "value" pairs whose name says they are secret.
_SECRETS = re.compile(
    r"(?P<token>\b(?:sk-(?:ant-)?|gsk_|xai-|gh[pousr]_|github_pat_)[A-Za-z0-9_\-]{16,})"
    r"|(?P<bearer>\bBearer\s+)[A-Za-z0-9._\-]{16,}"
    r"|(?P<pair>(?:api[_-]?key|token|secret|password)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]{8,}",
    re.IGNORECASE,
)

# Libraries that log every request at INFO; their warnings still come through.
_QUIET = ("httpx", "httpcore", "LiteLLM", "LiteLLM Router", "LiteLLM Proxy", "litellm")


def redact(text: str) -> str:
    def mask(match: re.Match[str]) -> str:
        if match.group("token"):
            return match.group("token")[:4] + "***"
        prefix = match.group("bearer") or match.group("pair") or ""
        return prefix + "***"

    return _SECRETS.sub(mask, text)


def _extras(record: logging.LogRecord) -> dict[str, object]:
    return {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": redact(record.getMessage()),
            **current_fields(),
            **_extras(record),
        }
        if record.exc_info:
            entry["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = redact(super().format(record))
        fields = {**current_fields(), **_extras(record)}
        if fields:
            line += "  " + " ".join(f"{k}={v}" for k, v in fields.items())
        return line

    def formatException(self, ei: object) -> str:  # noqa: N802 - logging's name
        return redact(super().formatException(ei))  # type: ignore[arg-type]


_stdout: TextIO | None = None


def _utf8_stdout() -> TextIO:
    """stdout as UTF-8 whatever the console's code page (Windows defaults to
    cp1252, which can't encode arq's arrows or a non-English PR title), so a
    log line is never lost to an encoding error. Made once: a second wrapper
    would close the first's buffer, stdout itself, when it's collected."""
    global _stdout
    if _stdout is None:
        buffer = getattr(sys.stdout, "buffer", None)
        utf8 = (sys.stdout.encoding or "").lower().replace("-", "") == "utf8"
        _stdout = (
            sys.stdout
            if buffer is None or utf8
            else io.TextIOWrapper(
                buffer, encoding="utf-8", errors="backslashreplace", line_buffering=True
            )
        )
    return _stdout


def configure_logging(*, service: str, level: str = "INFO", fmt: LogFormat = "text") -> None:
    """Send every logger, uvicorn's and arq's included, through one handler
    on stdout in the chosen format. Safe to call more than once."""
    handler = logging.StreamHandler(_utf8_stdout())
    handler.setFormatter(JsonFormatter(service) if fmt == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "arq"):
        lib = logging.getLogger(name)
        lib.handlers = []
        lib.propagate = True
    # The API logs each request itself, with its request id and duration.
    logging.getLogger("uvicorn.access").disabled = True
    for name in _QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)


def default_format(environment: str) -> LogFormat:
    return "json" if environment == "production" else "text"
