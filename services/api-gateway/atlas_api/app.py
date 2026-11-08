"""HTTP surface. Every route name, parameter name and status code is fixed by
contracts/api-gateway.md; the dashboard and the Telegram bot both read this shape.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from atlas_api.auth import (
    SESSION_COOKIE,
    AuthError,
    AuthService,
    Session,
    requires_confirm,
)
from atlas_api.repo import Repo, ServiceHealth, serialize_positions
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

BARS_PAGE_LIMIT = 5000
ALLOWED_TIMEFRAMES = frozenset({"1m", "5m", "15m", "1h", "4h", "1d"})


def _error(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details}},
    )


def _params_hash(body: dict[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _decimal(value: Any, field: str) -> Decimal:
    if value is None:
        raise HTTPException(status_code=422, detail=f"{field} is required")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{field} is not a decimal") from exc
    if not parsed.is_finite():
        raise HTTPException(status_code=422, detail=f"{field} must be finite")
    return parsed


def _uuid(value: str, field: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field} is not a UUID") from exc


def create_app(*, auth: AuthService, repo: Repo) -> FastAPI:
    app = FastAPI(title="ATLAS gateway", version="0.1.0")
    app.state.auth = auth
    app.state.repo = repo

    def service(request: Request) -> AuthService:
        return request.app.state.auth  # type: ignore[no-any-return]

    async def session_of(request: Request, svc: AuthService = Depends(service)) -> Session:
        return await svc.authenticate(request.cookies.get(SESSION_COOKIE))

    async def confirmed(
        request: Request,
        session: Session = Depends(session_of),
        svc: AuthService = Depends(service),
    ) -> Session:
        if not requires_confirm(request.method, request.url.path):
            return session
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return session  # route-level validation reports the malformed body
        token = request.headers.get("x-atlas-confirm", "")
        await svc.authorize_mutation(
            session, request.method, request.url.path, _params_hash(body), token or None
        )
        return session

    @app.exception_handler(AuthError)
    async def _auth_error(_: Request, exc: AuthError) -> JSONResponse:
        status = {
            "AUTH_REQUIRED": 401,
            "CONFIRM_REQUIRED": 428,
            "CONFIRM_INVALID": 403,
            "CONFIRM_REPLAYED": 403,
            "AUTH_LOCKED": 429,
        }.get(exc.code, 401)
        response = _error(status, exc.code, exc.message)
        if exc.retry_after_s:
            response.headers["Retry-After"] = str(exc.retry_after_s)
        return response

    @app.post("/auth/login")
    async def login(request: Request, svc: AuthService = Depends(service)) -> Response:
        body = await request.json()
        session = await svc.login(
            str(body.get("username", "")),
            str(body.get("password", "")),
            str(body.get("totp", "")),
            passkey_assertion=body.get("passkey_assertion"),
        )
        response = JSONResponse({"username": session.username, "session_id": session.session_id})
        response.set_cookie(
            SESSION_COOKIE,
            session.session_id,
            max_age=8 * 3600,
            httponly=True,
            secure=True,
            samesite="strict",
            path="/",
        )
        return response

    @app.post("/auth/logout")
    async def logout(
        request: Request,
        session: Session = Depends(session_of),
        svc: AuthService = Depends(service),
    ) -> Response:
        await svc.logout(session.session_id)
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/health")
    async def health(request: Request) -> Any:
        repo_: Repo = request.app.state.repo
        services: list[ServiceHealth] = await repo_.services()
        mode = await repo_.current_mode()
        degraded = any(item.status != "OK" for item in services)
        return {
            "services": [
                {
                    "name": item.name,
                    "status": item.status,
                    "last_heartbeat_ms": item.last_heartbeat_ms,
                }
                for item in services
            ],
            "data": "DEGRADED" if degraded else "OK",
            "mode": mode["mode"],
        }

    @app.get("/mode")
    async def get_mode(request: Request, session: Session = Depends(session_of)) -> Any:
        _ = session
        repo_: Repo = request.app.state.repo
        current = await repo_.current_mode()
        history = await repo_.mode_history(20)
        return {"mode": current["mode"], "current": current, "history": history}

    @app.post("/mode")
    async def post_mode(
        request: Request,
        session: Session = Depends(confirmed),
    ) -> Any:
        repo_: Repo = request.app.state.repo
        body = await request.json()
        mode = str(body.get("mode", "")).upper()
        if mode not in {"PAPER", "CO_PILOT", "AUTONOMOUS", "HALTED"}:
            raise HTTPException(status_code=422, detail="mode is not a known stage")
        cap = body.get("capital_cap_idr")
        reason = str(body.get("reason_code", "OPERATOR_PROMOTION"))
        cap_value = _decimal(cap, "capital_cap_idr") if cap is not None else None
        if mode == "CO_PILOT" and cap_value is None:
            raise HTTPException(status_code=422, detail="CO_PILOT requires capital_cap_idr")
        try:
            appended = await repo_.append_mode(
                mode=mode,
                reason_code=reason,
                actor="OPERATOR",
                actor_ref=session.username,
                evidence_ref=body.get("evidence_ref"),
                capital_cap_idr=cap_value,
            )
        except Exception as exc:  # database guard rejects illegal transitions
            return _error(409, "MODE_TRANSITION_REFUSED", str(exc))
        return appended

    @app.get("/positions")
    async def get_positions(
        request: Request, status: str | None = None, session: Session = Depends(session_of)
    ) -> Any:
        _ = session
        repo_: Repo = request.app.state.repo
        return {"positions": serialize_positions(await repo_.positions(status))}

    @app.post("/positions/{position_id}/close")
    async def close_position(
        request: Request, position_id: str, session: Session = Depends(session_of)
    ) -> Any:
        _ = (request, session)
        _uuid(position_id, "position_id")
        return {"accepted": True, "position_id": position_id, "action": "CLOSE"}

    @app.put("/positions/{position_id}/stop")
    async def move_stop(
        request: Request, position_id: str, session: Session = Depends(session_of)
    ) -> Any:
        _ = session
        repo_: Repo = request.app.state.repo
        _uuid(position_id, "position_id")
        body = await request.json()
        new_stop = _decimal(body.get("new_stop_price"), "new_stop_price")
        if new_stop <= 0:
            raise HTTPException(status_code=422, detail="new_stop_price must be positive")
        _ = repo_
        return {"accepted": True, "position_id": position_id, "new_stop_price": str(new_stop)}

    @app.get("/orders")
    async def get_orders(
        request: Request,
        status: str | None = None,
        since: int | None = None,
        session: Session = Depends(session_of),
    ) -> Any:
        _ = session
        repo_: Repo = request.app.state.repo
        return {"orders": await repo_.orders(status, since)}

    @app.get("/market/pairs")
    async def get_pairs(request: Request, session: Session = Depends(session_of)) -> Any:
        _ = session
        repo_: Repo = request.app.state.repo
        return {"pairs": await repo_.pairs()}

    @app.get("/market/bars")
    async def get_bars(
        request: Request,
        pair: str = Query(min_length=3),
        tf: str = Query(min_length=2),
        from_: int = Query(alias="from"),
        to: int | None = None,
    ) -> Any:
        repo_: Repo = request.app.state.repo
        if tf not in ALLOWED_TIMEFRAMES:
            raise HTTPException(
                status_code=422, detail=f"tf must be one of {sorted(ALLOWED_TIMEFRAMES)}"
            )
        if to is not None and to < from_:
            raise HTTPException(status_code=422, detail="to must not precede from")
        now_ms = to if to is not None else int(time.time() * 1000)
        bars = await repo_.bars(pair.upper(), tf, from_, now_ms, BARS_PAGE_LIMIT)
        quote = await repo_.quote(pair.upper())
        return {
            "pair": pair.upper(),
            "timeframe": tf,
            "bars": [
                {
                    "ts": bar.ts,
                    "open": str(bar.open),
                    "high": str(bar.high),
                    "low": str(bar.low),
                    "close": str(bar.close),
                    "volume": str(bar.volume),
                }
                for bar in bars
            ],
            "truncated": len(bars) >= BARS_PAGE_LIMIT,
            "age_ms": (now_ms - bars[-1].ts) if bars else None,
            "last_quote": quote,
        }

    @app.get("/news")
    async def get_news(
        request: Request,
        since: int | None = None,
        pair: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        session: Session = Depends(session_of),
    ) -> Any:
        _ = session
        repo_: Repo = request.app.state.repo
        return {"items": await repo_.news(since, pair, limit)}

    @app.get("/decisions")
    async def get_decisions(
        request: Request,
        from_: int | None = Query(default=None, alias="from"),
        to: int | None = None,
        outcome: str | None = None,
        session: Session = Depends(session_of),
    ) -> Any:
        _ = session
        repo_: Repo = request.app.state.repo
        return {"cycles": await repo_.decisions(from_, to, outcome, 100)}

    @app.get("/decisions/{cycle_id}")
    async def get_decision(
        request: Request,
        cycle_id: str,
        format: str = "json",
        session: Session = Depends(session_of),
    ) -> Any:
        _ = session
        repo_: Repo = request.app.state.repo
        cycle = await repo_.decision(_uuid(cycle_id, "cycle_id"))
        if cycle is None:
            return _error(404, "NOT_FOUND", "no such decision cycle")
        if format == "md":
            lines = [
                f"# Cycle {cycle['cycle_id']}",
                "",
                f"- trigger: {cycle['trigger']}",
                f"- outcome: {cycle['outcome']}",
                f"- agent runs: {cycle['agent_run_count']}",
                f"- proposals: {cycle['proposal_count']}",
            ]
            return PlainTextResponse("\n".join(lines) + "\n", media_type="text/markdown")
        return cycle

    @app.get("/incidents")
    async def get_incidents(
        request: Request, state: str | None = "open", session: Session = Depends(session_of)
    ) -> Any:
        _ = session
        repo_: Repo = request.app.state.repo
        return {"incidents": await repo_.incidents(state)}

    @app.get("/audit")
    async def get_audit(
        request: Request,
        from_: int | None = Query(default=None, alias="from"),
        to: int | None = None,
        type: str | None = None,
    ) -> Any:
        repo_: Repo = request.app.state.repo
        rows = await repo_.audit(from_, to, type)
        chain_ok = all(
            row["prev_hash"] == rows[index - 1]["hash"] for index, row in enumerate(rows) if index
        )
        return {"records": rows, "chain_ok": chain_ok}

    @app.post("/killswitch")
    async def killswitch(request: Request, session: Session = Depends(session_of)) -> Any:
        repo_: Repo = request.app.state.repo
        appended = await repo_.append_mode(
            mode="HALTED",
            reason_code="KILL_SWITCH",
            actor="OPERATOR",
            actor_ref=session.username,
            evidence_ref=None,
            capital_cap_idr=None,
        )
        return {**appended, "halted": True}

    @app.post("/halt/lift")
    async def lift_halt(request: Request, session: Session = Depends(confirmed)) -> Any:
        repo_: Repo = request.app.state.repo
        body = await request.json()
        mode = str(body.get("mode", "PAPER")).upper()
        if mode not in {"PAPER", "CO_PILOT"}:
            raise HTTPException(status_code=422, detail="a halt lifts only into PAPER or CO_PILOT")
        cap = body.get("capital_cap_idr")
        cap_value = _decimal(cap, "capital_cap_idr") if cap is not None else None
        if mode == "CO_PILOT" and cap_value is None:
            raise HTTPException(status_code=422, detail="CO_PILOT requires capital_cap_idr")
        try:
            appended = await repo_.append_mode(
                mode=mode,
                reason_code=str(body.get("reason_code", "RECOVERY")),
                actor="OPERATOR",
                actor_ref=session.username,
                evidence_ref=body.get("evidence_ref"),
                capital_cap_idr=cap_value,
            )
        except Exception as exc:
            return _error(409, "MODE_TRANSITION_REFUSED", str(exc))
        return appended

    @app.put("/risk/config")
    async def put_risk_config(request: Request, session: Session = Depends(confirmed)) -> Any:
        _ = session
        body = await request.json()
        unknown = set(body) - {
            "max_position_pct",
            "max_portfolio_risk_pct",
            "max_concurrent_positions",
            "max_daily_trades",
            "min_rr_net",
            "stop_monitor_grace_s",
            "max_daily_loss_pct",
            "max_drawdown_pct",
            "slippage_buffer_pct",
        }
        if unknown:
            raise HTTPException(status_code=422, detail=f"unknown config keys: {sorted(unknown)}")
        for key, value in body.items():
            _decimal(value, key)
        return {"accepted": True, "requires_confirm": True, "keys": sorted(body)}

    @app.post("/promotion/confirm")
    async def promotion_confirm(request: Request, session: Session = Depends(confirmed)) -> Any:
        _ = session
        body = await request.json()
        if not body.get("evidence_ref"):
            raise HTTPException(status_code=422, detail="promotion requires an evidence_ref")
        return {"accepted": True, "stage": body.get("stage")}

    return app


confirm_dependency = Callable[[Request], Awaitable[Session]]
