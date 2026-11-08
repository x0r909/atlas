#!/usr/bin/env python3
"""Generate the terminal's TypeScript view of the wire schema (T017).

The dashboard types must never be hand-edited: a field that drifts silently between
backend and frontend is a bug the terminal cannot see. `make types` rewrites the file,
`make types-check` fails CI when the committed file is stale.
"""

from __future__ import annotations

import sys
import types
import typing
from pathlib import Path
from typing import Any

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages" / "schemas"), str(ROOT / "packages" / "common")]

TARGET = ROOT / "apps" / "terminal-web" / "src" / "lib" / "schema.d.ts"

PY_TO_TS = {
    "str": "string",
    "int": "number",
    "float": "number",
    "bool": "boolean",
    "Decimal": "string",
    "datetime": "string",
    "date": "string",
    "UUID": "string",
    "Any": "unknown",
}

MODULES = [
    "atlas_schemas.venue",
    "atlas_schemas.order",
    "atlas_schemas.risk",
    "atlas_schemas.events",
]


def ts_type(annotation: Any) -> str:
    if annotation is type(None):
        return "null"
    if annotation is Any:
        return "unknown"
    if isinstance(annotation, str):
        return PY_TO_TS.get(annotation, annotation)
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        return " | ".join(ts_type(arg) for arg in args)
    if origin is list:
        return f"{ts_type(args[0])}[]"
    if origin is dict:
        return f"Record<string, {ts_type(args[1])}>"
    if origin is tuple:
        return "[" + ", ".join(ts_type(arg) for arg in args) + "]"
    if origin is typing.Literal:
        return " | ".join(ts_type(arg) if not isinstance(arg, str) else f'"{arg}"' for arg in args)
    if isinstance(annotation, type):
        if issubclass(annotation, BaseModel):
            return annotation.__name__
        return PY_TO_TS.get(annotation.__name__, annotation.__name__)
    return "unknown"


def render() -> str:
    import importlib

    models: dict[str, type[BaseModel]] = {}
    for module_name in MODULES:
        module = importlib.import_module(module_name)
        for name, value in vars(module).items():
            if (
                isinstance(value, type)
                and issubclass(value, BaseModel)
                and value is not BaseModel
                and not name.startswith("_")
            ):
                models[name] = value

    lines = [
        "/**",
        " * GENERATED FILE - do not edit.",
        " * Source: packages/schemas/atlas_schemas (pydantic models).",
        " * Regenerate with `make types`; `make types-check` fails when this file is stale.",
        " * A drifted field here means the terminal renders a shape the backend never sends.",
        " */",
        "",
    ]
    for name in sorted(models):
        model = models[name]
        lines.append(f"export interface {name} {{")
        for field_name, field in model.model_fields.items():
            optional = "" if field.is_required() else "?"
            lines.append(f"  {field_name}{optional}: {ts_type(field.annotation)};")
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    rendered = render()
    check = "--check" in sys.argv
    current = TARGET.read_text() if TARGET.exists() else ""
    if check:
        if current != rendered:
            print(f"stale: {TARGET.relative_to(ROOT)} - run `make types`", file=sys.stderr)
            return 1
        print("types are current")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(rendered)
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
