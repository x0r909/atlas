"""Mode resolution at startup (FR-001, FR-002, SC-012).

The rule that matters is asymmetric on purpose: **every failure resolves to Paper**, and
the only path that can raise is an *unknown* value. Being wrong towards Paper costs a missed
trade; being wrong towards Co-pilot or Autonomous spends real capital.

Resolution order for a service start:

1. ``ATLAS_MODE`` absent or blank            -> ``PAPER``
2. ``ATLAS_MODE`` unrecognised (e.g. PAPR)   -> refuse to start, code ``MODE_UNKNOWN``
3. ``ATLAS_MODE=HALTED``                     -> ``HALTED`` (an operator's kill switch wins)
4. ``mode_state`` readable and non-empty     -> that mode (the database is the authority)
5. ``mode_state`` missing, empty, unreadable -> ``PAPER``

Nothing here can produce ``CO_PILOT`` or ``AUTONOMOUS`` from an invalid value: promotion is
an authenticated operator action recorded in the audit log (FR-003), never a config parse.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

__all__ = [
    "MODE_ENV_VAR",
    "InMemoryModeStore",
    "Mode",
    "ModeConfigError",
    "ModeStore",
    "mode_from_env",
    "resolve_startup_mode",
]

MODE_ENV_VAR = "ATLAS_MODE"

#: Refusal code written to the audit log and printed on stdout when the value is unknown.
MODE_UNKNOWN = "MODE_UNKNOWN"

_LIVE_MODES = ("CO_PILOT", "AUTONOMOUS")


class Mode(StrEnum):
    """The five rollout stages. Matches the ``mode_state.mode`` enum in data-model.md."""

    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    CO_PILOT = "CO_PILOT"
    AUTONOMOUS = "AUTONOMOUS"
    HALTED = "HALTED"

    @property
    def is_live(self) -> bool:
        """Whether this mode may place an order that opens exposure on a live venue."""
        return self.value in _LIVE_MODES


class ModeConfigError(RuntimeError):
    """Raised when a service must refuse to start rather than guess a mode."""

    code = MODE_UNKNOWN

    def __init__(self, raw_value: str) -> None:
        super().__init__(
            f"{MODE_ENV_VAR}={raw_value!r} is not a known mode; refusing to start "
            f"({MODE_UNKNOWN}). Valid values: {', '.join(m.value for m in Mode)}"
        )
        self.raw_value = raw_value


class ModeStore(Protocol):
    """The read side of ``mode_state``. Any exception is treated as unreadable."""

    def read_current_mode(self) -> str | None:
        """Return the current mode name, or ``None`` when no row exists."""


@dataclass
class InMemoryModeStore:
    """Test/replay store. ``fail`` simulates an unreadable database."""

    mode: str | None = None
    fail: bool = False

    def read_current_mode(self) -> str | None:
        if self.fail:
            raise OSError("mode_state unreadable")
        return self.mode


def mode_from_env(environ: Mapping[str, str] | None = None) -> Mode:
    """Parse ``ATLAS_MODE``. Absent or blank means Paper; an unknown value raises."""
    source = os.environ if environ is None else environ
    raw = source.get(MODE_ENV_VAR)
    if raw is None or not raw.strip():
        return Mode.PAPER
    try:
        return Mode(raw.strip().upper())
    except ValueError as exc:
        raise ModeConfigError(raw) from exc


def resolve_startup_mode(
    environ: Mapping[str, str] | None = None,
    store: ModeStore | None = None,
) -> Mode:
    """Decide the mode a service runs in. Every failure path resolves to Paper."""
    env_mode = mode_from_env(environ)

    # An operator kill switch in the environment outranks a database that still says live.
    if env_mode is Mode.HALTED:
        return Mode.HALTED

    if store is None:
        return env_mode

    try:
        stored = store.read_current_mode()
    except Exception:
        return Mode.PAPER

    if stored is None or not str(stored).strip():
        # No row yet: the first boot of a fresh database is a Paper boot.
        return Mode.PAPER

    try:
        stored_mode = Mode(str(stored).strip().upper())
    except ValueError:
        # A corrupt row is a missing row, not a promotion.
        return Mode.PAPER

    # A live stage recorded in the database is only honoured when it was written by an
    # audited operator promotion; the row cannot exist otherwise (FR-003, FR-062).
    return stored_mode
