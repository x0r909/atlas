"""Path-preserving shim: the redaction logger lives in :mod:`atlas_common.log`.

``plan.md`` names this file, so the path is kept importable, but the implementation sits in
``atlas_common/log.py``. A module literally called ``logging`` inside a package shadows the
stdlib module for some static analysers and makes ``import logging`` inside it ambiguous —
a footgun in the one module that must never lose a secret on the way to a stream.

Import from either name; both refer to the same objects.
"""

from __future__ import annotations

from atlas_common.log import (
    ENV_ASSIGNMENT_RE as ENV_ASSIGNMENT_RE,
)
from atlas_common.log import (
    REDACTED as REDACTED,
)
from atlas_common.log import (
    SENSITIVE_KEY_RE as SENSITIVE_KEY_RE,
)
from atlas_common.log import (
    RedactionFilter as RedactionFilter,
)
from atlas_common.log import (
    configure_logging as configure_logging,
)
from atlas_common.log import (
    is_sensitive_key as is_sensitive_key,
)
from atlas_common.log import (
    redact_value as redact_value,
)
from atlas_common.log import (
    redaction_processor as redaction_processor,
)

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
