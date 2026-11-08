"""Log redaction (T010, FR-040).

The test asserts the negative: a secret NEVER reaches the stream. It covers the three
shapes a leak actually takes in this codebase — a sensitive field, a dumped environment,
and a signed header inside an exception message.
"""

from __future__ import annotations

import io
import logging
import os

import pytest
from atlas_common.log import (
    REDACTED,
    RedactionFilter,
    configure_logging,
    is_sensitive_key,
    redact_value,
)

# Assembled from pieces so commit scanners do not read a live key here.
API_KEY = "sk_live_" + "9f8e7d6c5b4a39281706f5e4d3c2b1a0"
HMAC_KEY = "hmac-2b7f1c0d9e4a5b6c8d7e6f5a4b3c2d1e"
TOTP_SEED = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
PASSWORD = "correct-horse-battery-staple"


# --- key-based redaction ----------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "api_key",
        "API_KEY",
        "indodax_api_secret",
        "atlas_hmac_key",
        "secret",
        "password",
        "totp_seed",
        "private_key",
        "authorization",
        "Cookie",
        "x-atlas-confirm",
        "session_id",
    ],
)
def test_sensitive_keys_are_recognised(key: str) -> None:
    assert is_sensitive_key(key)


@pytest.mark.parametrize("key", ["symbol", "amount", "mode", "venue_id", "reason_code"])
def test_ordinary_keys_are_not_redacted(key: str) -> None:
    assert not is_sensitive_key(key)


def test_plain_field_is_replaced() -> None:
    cleaned = redact_value({"api_key": API_KEY, "symbol": "BTCIDR"})
    assert cleaned["api_key"] == REDACTED
    assert cleaned["symbol"] == "BTCIDR"
    assert API_KEY not in str(cleaned)


def test_nested_field_is_replaced() -> None:
    payload = {"request": {"headers": {"Authorization": f"Bearer {API_KEY}"}}}
    cleaned = redact_value(payload)
    assert API_KEY not in str(cleaned)
    assert REDACTED in str(cleaned)


def test_redaction_works_inside_lists() -> None:
    cleaned = redact_value({"keys": [{"totp_seed": TOTP_SEED}, {"hmac_key": HMAC_KEY}]})
    assert TOTP_SEED not in str(cleaned)
    assert HMAC_KEY not in str(cleaned)


# --- text-based redaction ---------------------------------------------------------------


def test_dumped_environment_is_redacted() -> None:
    dump = (
        f"PATH=/usr/bin\nINDODAX_API_KEY={API_KEY}\n"
        f"ATLAS_HMAC_SECRET={HMAC_KEY}\nATLAS_MODE=PAPER\n"
    )
    cleaned = redact_value(dump)
    assert API_KEY not in cleaned
    assert HMAC_KEY not in cleaned
    assert "ATLAS_MODE=PAPER" in cleaned  # non-secret values survive


def test_bearer_token_in_a_message_is_redacted() -> None:
    cleaned = redact_value(f"upstream rejected Authorization: Bearer {API_KEY}")
    assert API_KEY not in cleaned


def test_exception_message_is_redacted() -> None:
    try:
        raise RuntimeError(f"venue rejected key {API_KEY}")
    except RuntimeError as exc:
        cleaned = redact_value(exc)
    assert API_KEY not in str(cleaned)


def test_deep_nesting_is_truncated_rather_than_leaked() -> None:
    payload: dict[str, object] = {"docker": {"dump": "x"}}
    node = payload
    for _ in range(12):
        child: dict[str, object] = {"dump": "x"}
        node["next"] = child
        node = child
    cleaned = redact_value(payload)
    assert "[TRUNCATED]" in str(cleaned)


# --- the stream itself ------------------------------------------------------------------


def test_secret_never_reaches_the_configured_stream() -> None:
    buffer = io.StringIO()
    configure_logging("test-service", "INFO", stream=buffer)

    structlog = pytest.importorskip("structlog")
    log = structlog.get_logger()
    log.info(
        "venue auth failed",
        api_key=API_KEY,
        hmac_key=HMAC_KEY,
        totp_seed=TOTP_SEED,
        password=PASSWORD,
        symbol="BTCIDR",
    )

    output = buffer.getvalue()
    assert output, "configure_logging produced no output at all"
    for secret in (API_KEY, HMAC_KEY, TOTP_SEED, PASSWORD):
        assert secret not in output, f"secret reached the log stream: {secret}"
    assert "BTCIDR" in output
    structlog.reset_defaults()


def test_stdlib_filter_redacts_records() -> None:
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.addFilter(RedactionFilter())
    logger = logging.getLogger("atlas-test-stdlib")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.info("auth %s", {"api_key": API_KEY})
    logger.info(f"env INDODAX_API_KEY={API_KEY}")
    logger.info("plain event %s", "BTCIDR")

    output = buffer.getvalue()
    assert API_KEY not in output
    assert "BTCIDR" in output


def test_invalid_level_is_refused() -> None:
    with pytest.raises(ValueError, match="log level"):
        configure_logging("test-service", "LOUD", stream=io.StringIO())


def test_configure_logging_defaults_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("test-service")
    structlog = pytest.importorskip("structlog")
    structlog.get_logger().info("hello", api_key=API_KEY)
    captured = capsys.readouterr()
    assert "hello" in captured.err
    assert API_KEY not in captured.err
    structlog.reset_defaults()


def test_environment_is_not_logged_implicitly() -> None:
    """A service must not log os.environ wholesale; if it does, redaction still holds."""
    os.environ["ATLAS_TEST_FAKE_API_KEY"] = API_KEY
    try:
        cleaned = redact_value(dict(os.environ))
    finally:
        del os.environ["ATLAS_TEST_FAKE_API_KEY"]
    assert API_KEY not in str(cleaned)


def test_the_planned_path_is_the_same_module() -> None:
    """`atlas_common.logging` (named in plan.md) must stay importable and identical."""
    import atlas_common.log as implementation
    import atlas_common.logging as shim

    assert shim.configure_logging is implementation.configure_logging
    assert shim.REDACTED == implementation.REDACTED
