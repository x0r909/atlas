"""One golden book, so a rule test only has to name the single thing it changes."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from atlas_risk.models import ProposedTrade, Quote, RiskConfig, RiskSnapshot
from atlas_schemas.venue import VenueProfile

NOW = datetime(2024, 5, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def profile() -> VenueProfile:
    return VenueProfile(
        venue_id="indodax",
        version=1,
        fee_maker_pct=Decimal("0.200"),
        fee_taker_pct=Decimal("0.300"),
        cfx_fee_pct=Decimal("0.300"),
        cfx_fee_pct_assumed=Decimal("0.300"),
        tax_buy_pct_assumed=Decimal("0.210"),
        tax_pph_pct=Decimal("0.100"),
        tax_ppn_pct=Decimal("11.000"),
        tax_applies_to="SELL_ONLY",
        min_notional_idr=Decimal("10000"),
        max_leverage=1,
        native_stop_orders=False,
        cost_model_version="1",
    )


@pytest.fixture
def config() -> RiskConfig:
    return RiskConfig(version=1)


@pytest.fixture
def quote() -> Quote:
    return Quote(
        bid=Decimal("999"),
        ask=Decimal("1000"),
        last=Decimal("1000"),
        age_ms=1_000,
        median_spread_24h=Decimal("2"),
    )


@pytest.fixture
def snapshot(quote: Quote) -> RiskSnapshot:
    return RiskSnapshot(
        now=NOW,
        venue_live_enabled=True,
        market_type="SPOT",
        quote=quote,
        equity_idr=Decimal("1000000"),
        day_start_equity_idr=Decimal("1000000"),
        peak_equity_idr=Decimal("1000000"),
    )


@pytest.fixture
def trade() -> ProposedTrade:
    return ProposedTrade(
        proposal_id=UUID("11111111-1111-1111-1111-111111111111"),
        cycle_id=uuid4(),
        venue_id="indodax",
        pair="BTCIDR",
        side="BUY",
        entry_price=Decimal("1000"),
        stop_price=Decimal("950"),
        target_price=Decimal("1200"),
        horizon_hours=24,
        intended_notional_idr=Decimal("100000"),
        confidence=Decimal("0.7"),
        evidence_kinds=("BAR",),
        evidence_tiers=(None,),
    )
