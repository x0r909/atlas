"""Fixtures every later test file shares, so a test is one file and not one harness.

Nothing here creates infrastructure: the Postgres and Redis fixtures attach to what
``docker compose -f infra/compose/docker-compose.yml up -d`` already provides, and skip when
it is absent. The simulated venue and the fake clock are pure Python.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from atlas_schemas.risk import RiskVerdict
from atlas_schemas.verdict_sign import sign_verdict

from tests.support.simulated_venue import SimulatedVenue

TEST_SIGNING_KEY = "test-key-0123456789abcdef0123456789abcdef"


@dataclass
class FakeClock:
    """A clock a test can move, in the units the code under test uses."""

    start: int = 1_700_000_000
    _offset: int = field(default=0, init=False, repr=False)

    def now(self) -> int:
        return self.start + self._offset

    def now_ms(self) -> int:
        return self.now() * 1000

    def utc(self) -> datetime:
        return datetime.fromtimestamp(self.now(), UTC)

    def advance(self, seconds: int) -> None:
        self._offset += seconds


@pytest.fixture()
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def simulated_venue() -> SimulatedVenue:
    return SimulatedVenue(price=Decimal("1000"))


@pytest.fixture()
def signing_key() -> str:
    return TEST_SIGNING_KEY


@pytest.fixture()
def verdict_factory(signing_key: str, fake_clock: FakeClock) -> Callable[..., RiskVerdict]:
    """A signed, approved verdict with every field an execution path reads."""

    def make(**overrides: Any) -> RiskVerdict:
        now = datetime.fromtimestamp(fake_clock.now(), UTC)
        base: dict[str, Any] = {
            "verdict_id": uuid4(),
            "proposal_id": uuid4(),
            "approved": True,
            "rule_results": [],
            "sized_quantity": Decimal("0.01"),
            "sized_notional_idr": Decimal("10"),
            "realised_risk_idr": Decimal("5"),
            "realised_risk_pct": Decimal("0.1"),
            "rr_net": Decimal("2.0"),
            "cost_model_source": "VERIFIED",
            "risk_config_version": 1,
            "venue_profile_id": 1,
            "portfolio_snapshot_id": uuid4(),
            "input_hash": "0" * 64,
            "verdict_at": now,
            "expires_at": now + timedelta(seconds=60),
        }
        base.update(overrides)
        unsigned = RiskVerdict.model_construct(**base)
        signature = sign_verdict(unsigned.model_dump(mode="python"), signing_key)
        return RiskVerdict.model_validate({**base, "signature": signature})

    return make


@pytest.fixture(scope="session")
def dsn() -> str:
    value = os.environ.get("ATLAS_DB_DSN", "")
    if not value:
        pytest.skip("ATLAS_DB_DSN is not set; this test needs a migrated Postgres")
    return value


@pytest.fixture(scope="session")
def redis_password() -> str:
    return os.environ.get("REDIS_PASSWORD", "dev")


@pytest.fixture()
def ids() -> Iterator[Callable[[], UUID]]:
    yield uuid4


@pytest.fixture()
def real_clock() -> Callable[[], float]:
    return time.time
