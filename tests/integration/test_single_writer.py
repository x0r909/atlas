"""T066: only execution may reach the venue's order API, and every mutation is audited once.

The import boundary is checked by parsing the source, not by trusting convention: a new service
that calls `place_order` fails here before it can ever run.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = {
    ROOT / "services/execution/atlas_execution/orders.py",
}
VENUE_CALLS = {"place_order", "cancel_order"}


def _files() -> list[Path]:
    return [p for p in ROOT.rglob("*.py") if "_review" not in p.parts and ".venv" not in p.parts]


def test_only_the_execution_writer_calls_the_venue_order_api() -> None:
    offenders: list[str] = []
    for path in _files():
        if path in ALLOWED or "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in VENUE_CALLS:
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} calls {name}()")
    assert not offenders, "\n".join(offenders)


def test_the_writer_is_the_only_module_that_imports_the_port_methods() -> None:
    """Reads may use the port anywhere; the mutation entry points stay in one file."""
    for path in _files():
        if path in ALLOWED or "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert "client_order_id" not in names, f"{path.relative_to(ROOT)} rebuilds order ids"
