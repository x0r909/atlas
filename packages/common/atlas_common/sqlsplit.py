"""Split a reviewed SQL script into individual statements.

asyncpg prepares every statement, so a migration that carries several DDL statements in
one string fails with "cannot insert multiple commands into a prepared statement". The
naive fix, ``sql.split(";")``, corrupts PL/pgSQL bodies, which contain semicolons inside
dollar quotes. This splitter tracks dollar-quoted blocks, single-quoted strings, line
comments and block comments, and only breaks on a top-level semicolon.
"""

from __future__ import annotations

_DOLLAR = "$"


def split_statements(sql: str) -> list[str]:
    """Return the non-empty statements of ``sql``, in order, without trailing ``;``."""
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    dollar_tag: str | None = None
    in_single = False
    in_line_comment = False
    in_block_comment = False

    while i < n:
        ch = sql[i]
        pair = sql[i : i + 2]

        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            if pair == "*/":
                buf.append(pair)
                in_block_comment = False
                i += 2
                continue
            buf.append(ch)
            i += 1
            continue

        if dollar_tag is not None:
            if sql.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
            buf.append(ch)
            i += 1
            continue

        if in_single:
            buf.append(ch)
            if ch == "'":
                # '' is an escaped quote inside a string literal.
                if sql[i + 1 : i + 2] == "'":
                    buf.append("'")
                    i += 2
                    continue
                in_single = False
            i += 1
            continue

        if pair == "--":
            in_line_comment = True
            buf.append(pair)
            i += 2
            continue

        if pair == "/*":
            in_block_comment = True
            buf.append(pair)
            i += 2
            continue

        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue

        if ch == _DOLLAR:
            tag_end = sql.find(_DOLLAR, i + 1)
            if tag_end != -1:
                candidate = sql[i : tag_end + 1]
                inner = candidate[1:-1]
                if inner == "" or inner.replace("_", "").isalnum():
                    dollar_tag = candidate
                    buf.append(candidate)
                    i = tag_end + 1
                    continue
            buf.append(ch)
            i += 1
            continue

        if ch == ";":
            statement = "".join(buf).strip()
            if statement:
                statements.append(statement)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def apply_statements(bind: object, script: str) -> None:
    """Execute every statement of ``script`` on an alembic bind, one at a time."""
    from sqlalchemy import text

    execute = getattr(bind, "execute", None)
    if execute is None:  # pragma: no cover - alembic always passes a Connection
        raise TypeError("apply_statements requires a SQLAlchemy Connection")
    for statement in split_statements(script):
        execute(text(statement))  # nosec B608: constant DDL from a reviewed migration


__all__ = ["apply_statements", "split_statements"]
