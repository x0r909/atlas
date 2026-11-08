"""Postgres read/write surface used by the gateway routes.

All timestamps cross this module as epoch milliseconds: the wire format is ms everywhere,
and a database column that carries a timestamptz is converted at the boundary rather than
leaking a driver object into a response.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class PositionView:
    position_id: str
    venue_id: str
    pair: str
    status: str
    quantity: Decimal
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal | None
    opened_at_ms: int
    stop_confirmation: str
    unrealised_pnl_idr: Decimal | None


@dataclass(frozen=True, slots=True)
class ServiceHealth:
    name: str
    status: str
    last_heartbeat_ms: int | None


@dataclass(frozen=True, slots=True)
class BarView:
    pair: str
    timeframe: str
    ts: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class Repo(Protocol):
    async def current_mode(self) -> dict[str, Any]: ...

    async def mode_history(self, limit: int) -> list[dict[str, Any]]: ...

    async def append_mode(
        self,
        *,
        mode: str,
        reason_code: str,
        actor: str,
        evidence_ref: str | None,
        capital_cap_idr: Decimal | None,
    ) -> dict[str, Any]: ...

    async def positions(self, status: str | None) -> list[PositionView]: ...

    async def orders(self, status: str | None, since_ms: int | None) -> list[dict[str, Any]]: ...

    async def pairs(self) -> list[dict[str, Any]]: ...

    async def bars(
        self, pair: str, timeframe: str, from_ms: int, to_ms: int, limit: int
    ) -> list[BarView]: ...

    async def quote(self, pair: str) -> dict[str, Any] | None: ...

    async def decisions(
        self, from_ms: int | None, to_ms: int | None, outcome: str | None, limit: int
    ) -> list[dict[str, Any]]: ...

    async def decision(self, cycle_id: str) -> dict[str, Any] | None: ...

    async def incidents(self, state: str | None) -> list[dict[str, Any]]: ...

    async def news(
        self, since_ms: int | None, pair: str | None, limit: int
    ) -> list[dict[str, Any]]: ...

    async def audit(
        self, from_ms: int | None, to_ms: int | None, record_type: str | None
    ) -> list[dict[str, Any]]: ...

    async def services(self) -> list[ServiceHealth]: ...


def _ms(value: Any) -> int | None:
    if value is None:
        return None
    return int(value.timestamp() * 1000) if hasattr(value, "timestamp") else int(value)


class AsyncpgRepo:
    """One place where SQL lives; every query is parameterised, no string interpolation."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def current_mode(self) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT mode, reason_code, capital_cap_idr, actor, created_at
                FROM mode_state ORDER BY created_at DESC, id DESC LIMIT 1
                """
            )
        if row is None:
            raise RuntimeError("mode_state is empty - migration 0005 seeds the first row")
        return {
            "mode": row["mode"],
            "reason_code": row["reason_code"],
            "capital_cap_idr": str(row["capital_cap_idr"]) if row["capital_cap_idr"] else None,
            "actor": row["actor"],
            "since_ms": _ms(row["created_at"]),
        }

    async def mode_history(self, limit: int) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT mode, previous_mode, reason_code, capital_cap_idr, actor, evidence_ref,
                       created_at
                FROM mode_state ORDER BY created_at DESC, id DESC LIMIT $1
                """,
                limit,
            )
        return [
            {
                "mode": row["mode"],
                "previous_mode": row["previous_mode"],
                "reason_code": row["reason_code"],
                "capital_cap_idr": str(row["capital_cap_idr"]) if row["capital_cap_idr"] else None,
                "actor": row["actor"],
                "evidence_ref": row["evidence_ref"],
                "created_at_ms": _ms(row["created_at"]),
            }
            for row in rows
        ]

    async def append_mode(
        self,
        *,
        mode: str,
        reason_code: str,
        actor: str,
        evidence_ref: str | None,
        capital_cap_idr: Decimal | None,
    ) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO mode_state (mode, reason_code, actor, evidence_ref, capital_cap_idr)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING mode, reason_code, actor, capital_cap_idr, created_at
                """,
                mode,
                reason_code,
                actor,
                evidence_ref,
                capital_cap_idr,
            )
        return {
            "mode": row["mode"],
            "reason_code": row["reason_code"],
            "actor": row["actor"],
            "capital_cap_idr": str(row["capital_cap_idr"]) if row["capital_cap_idr"] else None,
            "since_ms": _ms(row["created_at"]),
        }

    async def positions(self, status: str | None) -> list[PositionView]:
        query = """
            SELECT p.id, p.venue_id, p.pair, p.status, p.quantity, p.entry_price, p.stop_price,
                   p.target_price, p.opened_at, p.stop_confirmation
            FROM position p
        """
        args: list[Any] = []
        if status:
            query += " WHERE p.status = $1"
            args.append(status.upper())
        query += " ORDER BY p.opened_at DESC LIMIT 200"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
        return [
            PositionView(
                position_id=str(row["id"]),
                venue_id=row["venue_id"],
                pair=row["pair"],
                status=row["status"],
                quantity=row["quantity"],
                entry_price=row["entry_price"],
                stop_price=row["stop_price"],
                target_price=row["target_price"],
                opened_at_ms=_ms(row["opened_at"]) or 0,
                stop_confirmation=row["stop_confirmation"],
                unrealised_pnl_idr=None,
            )
            for row in rows
        ]

    async def orders(self, status: str | None, since_ms: int | None) -> list[dict[str, Any]]:
        query = """
            SELECT id, pair, side, type, status, quantity, filled_quantity, limit_price,
                   created_at
            FROM "order"
        """
        clauses: list[str] = []
        args: list[Any] = []
        if status:
            args.append(status.upper())
            clauses.append(f"status = ${len(args)}")
        if since_ms is not None:
            args.append(since_ms)
            clauses.append(f"created_at >= to_timestamp(${len(args)} / 1000.0)")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT 200"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
        return [
            {
                "order_id": str(row["id"]),
                "pair": row["pair"],
                "side": row["side"],
                "type": row["type"],
                "status": row["status"],
                "quantity": str(row["quantity"]),
                "filled_quantity": str(row["filled_quantity"]),
                "limit_price": str(row["limit_price"]) if row["limit_price"] else None,
                "created_at_ms": _ms(row["created_at"]),
            }
            for row in rows
        ]

    async def pairs(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT pair, base, quote, tick_size, step_size, min_notional, min_qty, enabled
                FROM venue_profile
                """
            )
        return [
            {
                "pair": row["pair"],
                "base": row["base"],
                "quote": row["quote"],
                "tick_size": str(row["tick_size"]),
                "step_size": str(row["step_size"]),
                "min_notional": str(row["min_notional"]),
                "min_qty": str(row["min_qty"]),
                "enabled": row["enabled"],
            }
            for row in rows
        ]

    async def bars(
        self, pair: str, timeframe: str, from_ms: int, to_ms: int, limit: int
    ) -> list[BarView]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT pair, timeframe, ts, open, high, low, close, volume
                FROM market_bar
                WHERE pair = $1 AND timeframe = $2
                  AND ts >= to_timestamp($3 / 1000.0) AND ts <= to_timestamp($4 / 1000.0)
                ORDER BY ts DESC
                LIMIT $5
                """,
                pair,
                timeframe,
                from_ms,
                to_ms,
                limit,
            )
        return [
            BarView(
                pair=row["pair"],
                timeframe=row["timeframe"],
                ts=_ms(row["ts"]) or 0,
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
            )
            for row in reversed(rows)
        ]

    async def quote(self, pair: str) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT pair, bid, ask, last, ts FROM quote_snapshot WHERE pair = $1", pair
            )
        if row is None:
            return None
        return {
            "pair": row["pair"],
            "bid": str(row["bid"]),
            "ask": str(row["ask"]),
            "last": str(row["last"]),
            "ts": _ms(row["ts"]),
        }

    async def decisions(
        self, from_ms: int | None, to_ms: int | None, outcome: str | None, limit: int
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if from_ms is not None:
            args.append(from_ms)
            clauses.append(f"started_at >= to_timestamp(${len(args)} / 1000.0)")
        if to_ms is not None:
            args.append(to_ms)
            clauses.append(f"started_at <= to_timestamp(${len(args)} / 1000.0)")
        if outcome:
            args.append(outcome.upper())
            clauses.append(f"outcome = ${len(args)}")
        args.append(limit)
        query = "SELECT id, trigger, outcome, started_at FROM decision_cycle"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += f" ORDER BY started_at DESC LIMIT ${len(args)}"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
        return [
            {
                "cycle_id": str(row["id"]),
                "trigger": row["trigger"],
                "outcome": row["outcome"],
                "started_at_ms": _ms(row["started_at"]),
            }
            for row in rows
        ]

    async def decision(self, cycle_id: str) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT c.id, c.trigger, c.outcome, c.started_at, c.finished_at,
                       (SELECT count(*) FROM trade_proposal p WHERE p.cycle_id = c.id) AS proposals,
                       (SELECT count(*) FROM agent_run a WHERE a.cycle_id = c.id) AS agent_runs
                FROM decision_cycle c WHERE c.id = $1::uuid
                """,
                cycle_id,
            )
        if row is None:
            return None
        return {
            "cycle_id": str(row["id"]),
            "trigger": row["trigger"],
            "outcome": row["outcome"],
            "started_at_ms": _ms(row["started_at"]),
            "finished_at_ms": _ms(row["finished_at"]),
            "proposal_count": row["proposals"],
            "agent_run_count": row["agent_runs"],
        }

    async def incidents(self, state: str | None) -> list[dict[str, Any]]:
        query = """
            SELECT id, class, kind, severity, state, auto_action, opened_at, summary
            FROM incident
        """
        args: list[Any] = []
        if state:
            args.append(state.lower())
            query += " WHERE state = $1"
        query += " ORDER BY opened_at DESC LIMIT 100"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
        return [
            {
                "incident_id": str(row["id"]),
                "class": row["class"],
                "kind": row["kind"],
                "severity": row["severity"],
                "state": row["state"],
                "auto_action": row["auto_action"],
                "opened_at_ms": _ms(row["opened_at"]),
                "summary": row["summary"],
            }
            for row in rows
        ]

    async def news(
        self, since_ms: int | None, pair: str | None, limit: int
    ) -> list[dict[str, Any]]:
        # News rows arrive with the news tables in a later phase; an empty list is honest.
        _ = (since_ms, pair, limit)
        return []

    async def audit(
        self, from_ms: int | None, to_ms: int | None, record_type: str | None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if from_ms is not None:
            args.append(from_ms)
            clauses.append(f"created_at >= to_timestamp(${len(args)} / 1000.0)")
        if to_ms is not None:
            args.append(to_ms)
            clauses.append(f"created_at <= to_timestamp(${len(args)} / 1000.0)")
        if record_type:
            args.append(record_type.upper())
            clauses.append(f"record_type = ${len(args)}")
        query = "SELECT seq, record_type, prev_hash, hash, created_at FROM audit_record"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY seq DESC LIMIT 500"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
        return [
            {
                "seq": row["seq"],
                "record_type": row["record_type"],
                "prev_hash": row["prev_hash"],
                "hash": row["hash"],
                "created_at_ms": _ms(row["created_at"]),
            }
            for row in reversed(rows)
        ]

    async def services(self) -> list[ServiceHealth]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT source_id, status, last_success_at FROM data_health ORDER BY source_id
                """
            )
        return [
            ServiceHealth(
                name=row["source_id"],
                status=row["status"],
                last_heartbeat_ms=_ms(row["last_success_at"]),
            )
            for row in rows
        ]


def serialize_positions(rows: Sequence[PositionView]) -> list[dict[str, Any]]:
    return [
        {
            "position_id": row.position_id,
            "venue_id": row.venue_id,
            "pair": row.pair,
            "status": row.status,
            "quantity": str(row.quantity),
            "entry_price": str(row.entry_price),
            "stop_price": str(row.stop_price),
            "target_price": str(row.target_price) if row.target_price else None,
            "opened_at_ms": row.opened_at_ms,
            "stop_confirmation": row.stop_confirmation,
            "unrealised_pnl_idr": (
                str(row.unrealised_pnl_idr) if row.unrealised_pnl_idr is not None else None
            ),
        }
        for row in rows
    ]
