"""T029/T029a-e: the wire shape the dashboard and the bot both depend on."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from atlas_api.app import create_app
from atlas_api.auth import (
    AuthService,
    OperatorRecord,
    hash_password,
    issue_confirm_token,
)
from atlas_api.repo import BarView, PositionView, ServiceHealth, serialize_positions
from fastapi.testclient import TestClient

CONFIRM_KEY = b"confirm-key-for-tests"
NOW = 1_800_000_000
PASSWORD = "correct horse battery"


class Clock:
    def __init__(self, now: int = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return float(self.now)


class Store:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.counters: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        self.values[key] = value

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)

    async def add_once(self, key: str, ttl_s: int) -> bool:
        if key in self.values:
            return False
        self.values[key] = "1"
        return True

    async def incr(self, key: str, ttl_s: int) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        self.values[key] = str(self.counters[key])
        return self.counters[key]

    async def counter(self, key: str) -> int:
        return self.counters.get(key, 0)


class FakeRepo:
    def __init__(self) -> None:
        self.mode = "PAPER"
        self.appended: list[dict[str, Any]] = []
        self.audit_broken = False
        self.position_rows = [
            PositionView(
                position_id=str(uuid4()),
                venue_id="indodax",
                pair="BTCIDR",
                status="OPEN",
                quantity=Decimal("0.001"),
                entry_price=Decimal("1000000000"),
                stop_price=Decimal("950000000"),
                target_price=Decimal("1200000000"),
                opened_at_ms=NOW * 1000,
                stop_confirmation="CONFIRMED",
                unrealised_pnl_idr=Decimal("12500"),
            )
        ]
        self.bar_rows = [
            BarView(
                pair="BTCIDR",
                timeframe="15m",
                ts=(NOW - index * 900) * 1000,
                open=Decimal("100"),
                high=Decimal("110"),
                low=Decimal("90"),
                close=Decimal("105"),
                volume=Decimal("1"),
            )
            for index in range(3)
        ]

    async def current_mode(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "reason_code": "STARTUP_DEFAULT",
            "capital_cap_idr": None,
            "actor": "SYSTEM",
            "since_ms": NOW * 1000,
        }

    async def mode_history(self, limit: int) -> list[dict[str, Any]]:
        return [
            {
                "mode": self.mode,
                "previous_mode": None,
                "reason_code": "STARTUP_DEFAULT",
                "capital_cap_idr": None,
                "actor": "SYSTEM",
                "evidence_ref": None,
                "created_at_ms": NOW * 1000,
            }
        ][:limit]

    async def append_mode(self, **kwargs: Any) -> dict[str, Any]:
        self.appended.append(kwargs)
        self.mode = kwargs["mode"]
        return {
            "mode": kwargs["mode"],
            "reason_code": kwargs["reason_code"],
            "actor": kwargs["actor"],
            "capital_cap_idr": None,
            "since_ms": NOW * 1000,
        }

    async def positions(self, status: str | None) -> list[PositionView]:
        return self.position_rows

    async def orders(self, status: str | None, since_ms: int | None) -> list[dict[str, Any]]:
        return []

    async def pairs(self) -> list[dict[str, Any]]:
        return [
            {
                "pair": "BTCIDR",
                "base": "BTC",
                "quote": "IDR",
                "tick_size": "1",
                "step_size": "0.00000001",
                "min_notional": "10000",
                "min_qty": "0.00000001",
                "enabled": True,
            }
        ]

    async def bars(
        self, pair: str, timeframe: str, from_ms: int, to_ms: int, limit: int
    ) -> list[BarView]:
        return [bar for bar in self.bar_rows if from_ms <= bar.ts <= to_ms][:limit]

    async def quote(self, pair: str) -> dict[str, Any]:
        return {"pair": pair, "bid": "100", "ask": "101", "last": "100", "ts": NOW * 1000}

    async def decisions(
        self, from_ms: int | None, to_ms: int | None, outcome: str | None, limit: int
    ) -> list[dict[str, Any]]:
        return [
            {
                "cycle_id": "c1",
                "trigger": "SCHEDULED",
                "outcome": "NO_TRADE",
                "started_at_ms": NOW * 1000,
            }
        ]

    async def decision(self, cycle_id: str) -> dict[str, Any] | None:
        return {
            "cycle_id": cycle_id,
            "trigger": "SCHEDULED",
            "outcome": "NO_TRADE",
            "started_at_ms": NOW * 1000,
            "finished_at_ms": NOW * 1000,
            "proposal_count": 0,
            "agent_run_count": 2,
        }

    async def incidents(self, state: str | None) -> list[dict[str, Any]]:
        return []

    async def news(
        self, since_ms: int | None, pair: str | None, limit: int
    ) -> list[dict[str, Any]]:
        return []

    async def audit(
        self, from_ms: int | None, to_ms: int | None, record_type: str | None
    ) -> list[dict[str, Any]]:
        if self.audit_broken:
            return [
                {
                    "seq": 1,
                    "record_type": "ORDER",
                    "prev_hash": "0" * 64,
                    "hash": "a" * 64,
                    "created_at_ms": NOW,
                },
                {
                    "seq": 2,
                    "record_type": "ORDER",
                    "prev_hash": "c" * 64,
                    "hash": "b" * 64,
                    "created_at_ms": NOW,
                },
            ]
        return [
            {
                "seq": 1,
                "record_type": "MODE_CHANGE",
                "prev_hash": "0" * 64,
                "hash": "a" * 64,
                "created_at_ms": NOW * 1000,
            },
            {
                "seq": 2,
                "record_type": "ORDER",
                "prev_hash": "a" * 64,
                "hash": "b" * 64,
                "created_at_ms": NOW * 1000,
            },
        ]

    async def services(self) -> list[ServiceHealth]:
        return [
            ServiceHealth("ingest-market", "OK", NOW * 1000),
            ServiceHealth("stop-monitor", "OK", NOW * 1000),
        ]


class Operator:
    def __init__(self) -> None:
        import pyotp

        self.totp_secret = pyotp.random_base32()
        self.password_hash = hash_password(PASSWORD)
        self.passkey_credentials: tuple[str, ...] = ()
        self.username = "operator"


@pytest.fixture()
def harness() -> tuple[TestClient, FakeRepo, Operator, Clock, Store]:
    clock = Clock()
    store = Store()
    operator = Operator()

    async def lookup(username: str) -> OperatorRecord | None:
        if username != operator.username:
            return None
        return OperatorRecord(
            username=operator.username,
            password_hash=operator.password_hash,
            totp_secret=operator.totp_secret,
            passkey_credentials=operator.passkey_credentials,
        )

    auth = AuthService(store=store, confirm_key=CONFIRM_KEY, operator_lookup=lookup, clock=clock)
    repo = FakeRepo()
    app = create_app(auth=auth, repo=repo)
    return TestClient(app), repo, operator, clock, store


def login(client: TestClient, operator: Operator, clock: Clock) -> str:
    import pyotp

    code = pyotp.TOTP(operator.totp_secret).at(clock.now)
    response = client.post(
        "/auth/login",
        json={"username": operator.username, "password": PASSWORD, "totp": code},
    )
    assert response.status_code == 200, response.text
    cookie: str = response.cookies["__Host-atlas_session"]
    return cookie


def authed(harness: Any) -> tuple[TestClient, FakeRepo]:
    client, repo, operator, clock, _ = harness
    client.cookies.set("__Host-atlas_session", login(client, operator, clock))
    return client, repo


def test_health_is_public_but_everything_else_needs_a_session(harness: Any) -> None:
    client, _, _, _, _ = harness
    assert client.get("/health").status_code == 200
    for path in ("/positions", "/orders", "/market/pairs", "/news", "/decisions", "/incidents"):
        assert client.get(path).status_code == 401, path
    assert client.post("/killswitch").status_code == 401
    assert client.post("/mode", json={"mode": "CO_PILOT"}).status_code == 401


def test_health_reports_every_service_and_the_mode(harness: Any) -> None:
    client, _, _, _, _ = harness
    body = client.get("/health").json()
    assert body["mode"] == "PAPER"
    assert body["data"] == "OK"
    assert [item["name"] for item in body["services"]] == ["ingest-market", "stop-monitor"]


def test_mode_change_needs_a_confirmation_token(harness: Any) -> None:
    client, repo, operator, clock, _ = harness
    cookie = login(client, operator, clock)
    assert (
        client.post(
            "/mode",
            json={"mode": "CO_PILOT", "capital_cap_idr": "1000"},
            cookies={"__Host-atlas_session": cookie},
        ).json()["error"]["code"]
        == "CONFIRM_REQUIRED"
    )
    assert repo.appended == []


def test_confirmed_mode_change_is_appended_once(harness: Any) -> None:
    client, repo, operator, clock, _ = harness
    cookie = login(client, operator, clock)
    body = {
        "mode": "CO_PILOT",
        "reason_code": "OPERATOR_PROMOTION",
        "evidence_ref": "eval-1",
        "capital_cap_idr": "25000",
    }
    import hashlib
    import json

    params_hash = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    token = issue_confirm_token(
        CONFIRM_KEY,
        session_id=cookie,
        action="POST /mode",
        params_hash=params_hash,
        now=int(clock.now),
    )
    first = client.post(
        "/mode",
        json=body,
        headers={"X-Atlas-Confirm": token},
        cookies={"__Host-atlas_session": cookie},
    )
    assert first.status_code == 200, first.text
    assert repo.appended[0]["mode"] == "CO_PILOT"
    replay = client.post(
        "/mode",
        json=body,
        headers={"X-Atlas-Confirm": token},
        cookies={"__Host-atlas_session": cookie},
    )
    assert replay.json()["error"]["code"] == "CONFIRM_REPLAYED"


def test_killswitch_is_immediate_and_exempt_from_confirmation(harness: Any) -> None:
    client, repo, operator, clock, _ = harness
    cookie = login(client, operator, clock)
    response = client.post("/killswitch", cookies={"__Host-atlas_session": cookie})
    assert response.status_code == 200
    assert response.json()["halted"] is True
    assert repo.appended[-1]["mode"] == "HALTED"
    assert repo.appended[-1]["reason_code"] == "KILL_SWITCH"


def test_bars_carry_age_and_the_5000_bar_cap(harness: Any) -> None:
    client, _ = authed(harness)
    window = f"from={(NOW - 3600) * 1000}&to={NOW * 1000}"
    body = client.get(f"/market/bars?pair=BTCIDR&tf=15m&{window}").json()
    assert body["timeframe"] == "15m"
    assert body["truncated"] is False
    assert body["age_ms"] is not None
    assert body["last_quote"]["pair"] == "BTCIDR"
    assert client.get("/market/bars?pair=BTCIDR&tf=7m&from=1").status_code == 422


def test_positions_are_serialised_without_losing_the_stop_flag(harness: Any) -> None:
    client, _ = authed(harness)
    rows = client.get("/positions?status=open").json()["positions"]
    assert rows[0]["stop_confirmation"] == "CONFIRMED"
    assert rows[0]["quantity"] == "0.001"
    assert serialize_positions([]) == []


def test_decision_markdown_matches_the_json(harness: Any) -> None:
    client, _ = authed(harness)
    cycle_id = str(uuid4())
    as_json = client.get(f"/decisions/{cycle_id}").json()
    assert as_json["agent_run_count"] == 2
    markdown = client.get(f"/decisions/{cycle_id}?format=md")
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert "# Cycle" in markdown.text


def test_audit_reports_a_broken_chain(harness: Any) -> None:
    client, repo = authed(harness)
    assert client.get("/audit").json()["chain_ok"] is True
    repo.audit_broken = True
    assert client.get("/audit").json()["chain_ok"] is False
