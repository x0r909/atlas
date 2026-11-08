"""Shared ATLAS runtime helpers.

Import the submodules you need; this package deliberately re-exports nothing so that a
service importing ``atlas_common.money`` never pulls structlog or pydantic into its
process by accident.
"""

from __future__ import annotations

__all__: list[str] = []
