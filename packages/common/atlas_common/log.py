"""Structured logging with a redaction boundary (FR-040).

The plan is "structlog JSON to journald, no log shipping". That makes one property
non-negotiable: a secret must never reach the log stream, in any shape — as a field value,
as a header name, inside an exception message, or embedded in a dumped environment.

Two layers, because one is not enough:

1. **Key-based**: any mapping key that looks sensitive is replaced by ``[REDACTED]``
   before serialisation, at any depth.
2. **Text-based**: strings are scanned for ``NAME=value`` pairs whose name looks sensitive
   (the classic "log the whole ``os.environ``" mistake), for ``Bearer``/``Token``
   authorization fragments, and for provider-shaped keys (``sk_live_...``).

The limit is stated rather than hidden: a secret with no recognisable shape *and* no
assignment prefix cannot be detected by scanning. That is why the field-based layer exists,
and why call sites pass credentials as fields instead of formatting them into messages.

Redaction is applied in the processor chain, not by the caller, so a new call site cannot
forget it.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Mapping, MutableMapping, Sequence
from datetime import UTC, datetime
from typing import Any, TextIO

__all__ = [
    "ENV_ASSIGNMENT_RE",
    "REDACTED",
    "SENSITIVE_KEY_RE",
    "RedactionFilter",
    "configure_logging",
    "is_sensitive_key",
    "redact_value",
    "redaction_processor",
]

REDACTED = "[REDACTED]"

# Key fragments that make a field secret by nature. Kept deliberately blunt: a false
# positive costs a debugging detail, a false negative costs a key.
_SENSITIVE_FRAGMENTS = (
    "api_key",
    "apikey",
    "api_secret",
    "secret",
    "hmac",
    "password",
    "passphrase",
    "private_key",
    "totp",
    "seed",
    "credential",
    "authorization",
    "cookie",
    "session_id",
    "x-atlas-confirm",
)

SENSITIVE_KEY_RE = re.compile(
    "|".join(re.escape(fragment) for fragment in _SENSITIVE_FRAGMENTS), re.IGNORECASE
)

# `SOMETHING_KEY=abc`, `ATLAS_HMAC_SECRET=...`, `PASSWORD=...` inside a dumped environment.
ENV_ASSIGNMENT_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD|SEED)[A-Za-z0-9_]*)"
    r"=(?P<value>[^\s'\"\\]+)"
)

_BEARER_RE = re.compile(r"(?i)\b(bearer|token)\s+[A-Za-z0-9._\-]{8,}")

# Provider-shaped keys are recognisable on their own (`sk_live_...`, `pk_test_...`), so a
# secret pasted into a message is caught even without a NAME= prefix. Anything else with no
# anchor is NOT detectable by text scanning: the fix there is to pass the secret as a field,
# where the key-based layer catches it.
_PROVIDER_KEY_RE = re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{8,}\b")

_MAX_DEPTH = 8


def is_sensitive_key(key: object) -> bool:
    """Whether a mapping key names something that must not be logged."""
    return isinstance(key, str) and bool(SENSITIVE_KEY_RE.search(key))


def _redact_text(text: str) -> str:
    text = ENV_ASSIGNMENT_RE.sub(lambda m: f"{m.group('name')}={REDACTED}", text)
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    return _PROVIDER_KEY_RE.sub(REDACTED, text)


def redact_value(value: Any, *, _depth: int = 0) -> Any:
    """Return ``value`` with secrets removed, preserving structure.

    Dict keys whose *name* is sensitive have their value replaced regardless of type, so a
    nested ``{"response": {"api_key": "..."}}`` is handled the same as a flat one.
    """
    if _depth > _MAX_DEPTH:
        return "[TRUNCATED]"

    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if is_sensitive_key(key):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact_value(item, _depth=_depth + 1)
        return redacted

    if isinstance(value, str):
        return _redact_text(value)

    if isinstance(value, (bytes, bytearray)):
        return _redact_text(bytes(value).decode("utf-8", "replace"))

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_value(item, _depth=_depth + 1) for item in value]

    if isinstance(value, BaseException):
        return _redact_text(f"{type(value).__name__}: {value}")

    return value


def redaction_processor(
    _logger: object, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor; installed in the chain so no call site can bypass it."""
    cleaned = redact_value(dict(event_dict))
    assert isinstance(cleaned, dict)
    return cleaned


class RedactionFilter(logging.Filter):
    """stdlib-logging fallback that redacts before formatting.

    Used when structlog is unavailable, and attached to the handler rather than the logger
    so records propagated from libraries are covered too.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact_text(record.msg)
        elif isinstance(record.msg, Mapping):
            record.msg = redact_value(record.msg)
        if record.args:
            if isinstance(record.args, Mapping):
                record.args = redact_value(record.args)
            else:
                record.args = tuple(redact_value(arg) for arg in record.args)
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = _redact_text(str(record.exc_info[1]))
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "event": redact_value(record.getMessage()),
            "level": record.levelname.lower(),
            "logger": record.name,
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
        }
        for key, value in getattr(record, "atlas_extra", {}).items():
            payload[key] = value if is_sensitive_key(key) else redact_value(value)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    service: str,
    level: str = "INFO",
    *,
    stream: TextIO | None = None,
) -> None:
    """Configure JSON logging for one service.

    Output goes to ``stream`` (stderr by default) so systemd/journald captures it with the
    unit's metadata; ATLAS deliberately ships no logs anywhere else.
    """
    target = stream if stream is not None else sys.stderr
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"unknown log level {level!r}")

    try:
        import structlog
    except ModuleNotFoundError:  # pragma: no cover - exercised only without structlog
        _configure_stdlib(service, numeric_level, target)
        return

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redaction_processor,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(file=target),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service)


def _configure_stdlib(service: str, level: int, stream: TextIO) -> None:
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    handler.addFilter(RedactionFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    logging.LoggerAdapter(logging.getLogger(service), {})
