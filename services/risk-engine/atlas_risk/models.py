"""Inputs to the gate. Nothing here touches a database, a clock, or a socket.

The engine is handed a snapshot; it never fetches one. That is what makes replay byte-exact
(contracts/decision-risk.md § 6) and what keeps a rule testable as a pure function.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from atlas_schemas.risk import TradeProposal

HEALTH_OK = "OK"
HEALTH_STALE = "STALE"
HEALTH_DOWN = "DOWN"


@dataclass(frozen=True, slots=True)
class Quote:
    """What the venue said last, and how long ago it said it."""

    bid: Decimal
    ask: Decimal
    last: Decimal
    age_ms: int
    median_spread_24h: Decimal

    def __post_init__(self) -> None:
        if self.bid <= 0 or self.ask <= 0 or self.last <= 0:
            raise ValueError("quote prices must be positive")
        if self.ask < self.bid:
            raise ValueError("crossed quote")
        if self.age_ms < 0:
            raise ValueError("quote age cannot be negative")

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid

    @property
    def spread_ratio(self) -> Decimal | None:
        """Spread as a multiple of the 24 h median; None when there is no median to compare."""
        if self.median_spread_24h <= 0:
            return None
        return self.spread / self.median_spread_24h

    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True, slots=True)
class RiskSnapshot:
    """Everything the gate may look at, captured at ``now``."""

    now: datetime
    venue_live_enabled: bool
    market_type: str
    quote: Quote
    equity_idr: Decimal
    day_start_equity_idr: Decimal
    peak_equity_idr: Decimal
    asset_exposure_idr: Decimal = Decimal("0")
    total_exposure_idr: Decimal = Decimal("0")
    open_positions: int = 0
    data_health: str = HEALTH_OK
    reconciliation_ok: bool = True
    macro_blackout: bool = False
    volatility_sigma: Decimal = Decimal("0")
    consecutive_losses: int = 0
    last_loss_at: datetime | None = None
    orders_last_hour: int = 0
    recent_proposal_ids: frozenset[UUID] = field(default_factory=frozenset)
    budget_exhausted: bool = False
    # True only when the order would go to a live venue: the PAPER mode must not be blocked by
    # a venue that is deliberately not live-enabled.
    require_live_venue: bool = False

    def __post_init__(self) -> None:
        if self.equity_idr <= 0:
            raise ValueError("equity must be positive")
        if self.day_start_equity_idr <= 0:
            raise ValueError("day-start equity must be positive")
        if self.peak_equity_idr <= 0:
            raise ValueError("peak equity must be positive")
        for name in ("asset_exposure_idr", "total_exposure_idr"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.open_positions < 0 or self.orders_last_hour < 0:
            raise ValueError("counts cannot be negative")

    @property
    def daily_loss_pct(self) -> Decimal:
        """Loss from the day's opening equity, as a positive percentage."""
        return (self.day_start_equity_idr - self.equity_idr) / self.day_start_equity_idr * 100

    @property
    def drawdown_pct(self) -> Decimal:
        return (self.peak_equity_idr - self.equity_idr) / self.peak_equity_idr * 100


@dataclass(frozen=True, slots=True)
class RiskConfig:
    """The operator-tunable knobs, one-for-one with ``risk_config``."""

    version: int
    risk_per_trade_pct: Decimal = Decimal("0.500")
    max_position_pct: Decimal = Decimal("25.000")
    max_asset_exposure_pct: Decimal = Decimal("50.000")
    max_total_exposure_pct: Decimal = Decimal("100.000")
    max_daily_loss_pct: Decimal = Decimal("2.000")
    max_drawdown_pct: Decimal = Decimal("10.000")
    min_rr_net: Decimal = Decimal("1.500")
    max_slippage_pct: Decimal = Decimal("0.300")
    max_spread_multiple: Decimal = Decimal("3.00")
    volatility_breaker_sigma: Decimal = Decimal("5.00")
    stale_price_seconds: int = 10
    cooldown_after_losses: int = 3
    cooldown_hours: int = 4
    time_stop_hours: int = 120
    max_open_positions: int = 5
    max_orders_per_hour: int = 6
    llm_daily_cap_idr: int = 5000
    llm_monthly_cap_idr: int = 250000


@dataclass(frozen=True, slots=True)
class ProposedTrade:
    """A proposal as the gate sees it: the model's stop may be missing, and that is a rejection.

    ``atlas_schemas.risk.TradeProposal`` validates the agent's envelope, which requires a stop.
    The gate still needs a shape in which "no stop" exists, because NO_STOP_LOSS is a rule with
    an audit trail, not a schema error.
    """

    proposal_id: UUID
    cycle_id: UUID
    venue_id: str
    pair: str
    side: str
    entry_price: Decimal
    stop_price: Decimal | None
    target_price: Decimal | None
    horizon_hours: int
    intended_notional_idr: Decimal
    confidence: Decimal
    evidence_kinds: tuple[str, ...] = ()
    evidence_tiers: tuple[int | None, ...] = ()

    @classmethod
    def from_model(cls, proposal: TradeProposal) -> ProposedTrade:
        return cls(
            proposal_id=proposal.proposal_id,
            cycle_id=proposal.cycle_id,
            venue_id=proposal.venue_id,
            pair=proposal.pair,
            side=proposal.side,
            entry_price=proposal.entry_price,
            stop_price=proposal.stop_price,
            target_price=proposal.target_price,
            horizon_hours=proposal.horizon_hours,
            intended_notional_idr=proposal.intended_notional_idr,
            confidence=proposal.confidence,
            evidence_kinds=tuple(item.kind for item in proposal.evidence),
            evidence_tiers=tuple(item.source_tier for item in proposal.evidence),
        )

    def stop_distance_pct(self) -> Decimal | None:
        if self.stop_price is None:
            return None
        return (self.entry_price - self.stop_price) / self.entry_price * 100
