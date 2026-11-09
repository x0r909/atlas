"""The gate itself: build the facts once, walk the catalogue in order, report every result."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from typing import Any

from atlas_common.hashchain import canonical_payload
from atlas_schemas.risk import RuleResult
from atlas_schemas.venue import VenueProfile

from atlas_risk import costs
from atlas_risk.models import ProposedTrade, RiskConfig, RiskSnapshot
from atlas_risk.rules import RULE_ORDER, RULES, RuleContext


@dataclass(frozen=True, slots=True)
class Evaluation:
    rule_results: tuple[RuleResult, ...]
    primary_rule_id: str | None
    sizing: costs.Sizing | None
    rr_net: Decimal | None
    input_hash: str

    @property
    def approved(self) -> bool:
        return self.primary_rule_id is None


def _plain(value: Any) -> Any:
    """Dataclasses, Decimals, datetimes and frozensets, in a shape canonical_payload accepts."""
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, frozenset | set):
        return sorted(_plain(item) for item in value)
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


def input_hash(
    *, trade: ProposedTrade, snapshot: RiskSnapshot, config: RiskConfig, profile: VenueProfile
) -> str:
    """A digest of exactly what was evaluated, so a replay can prove it saw the same inputs."""
    payload: dict[str, Any] = {
        "trade": _plain(trade),
        "snapshot": _plain(snapshot),
        "config": _plain(config),
        "profile": profile.model_dump(mode="json"),
    }
    return hashlib.sha256(canonical_payload(payload)).hexdigest()


def _net_rr(
    trade: ProposedTrade, profile: VenueProfile, max_slippage_pct: Decimal
) -> Decimal | None:
    if trade.stop_price is None or trade.target_price is None:
        return None
    unit_costs = costs.round_trip_costs(
        entry_notional=trade.entry_price,
        exit_notional=trade.target_price,
        profile=profile,
        max_slippage_pct=max_slippage_pct,
    )
    # One unit, so the round trip expressed in IDR is also the per-unit cost.
    return costs.net_rr(
        entry=trade.entry_price,
        stop=trade.stop_price,
        target=trade.target_price,
        cost_per_unit_idr=unit_costs.total_round_trip_idr,
    )


def build_context(
    *,
    trade: ProposedTrade,
    snapshot: RiskSnapshot,
    config: RiskConfig,
    profile: VenueProfile,
    step_size: Decimal,
) -> RuleContext:
    sizing = None
    if trade.stop_price is not None:
        try:
            sizing = costs.size(
                proposal=trade,  # type: ignore[arg-type]
                equity_idr=snapshot.equity_idr,
                risk_per_trade_pct=config.risk_per_trade_pct,
                max_position_pct=config.max_position_pct,
                min_notional_idr=profile.min_notional_idr,
                step_size=step_size,
                profile=profile,
                max_slippage_pct=config.max_slippage_pct,
            )
        except ValueError:
            sizing = None
    return RuleContext(
        snapshot=snapshot,
        trade=trade,
        config=config,
        profile=profile,
        sizing=sizing,
        rr_net=_net_rr(trade, profile, config.max_slippage_pct),
    )


def evaluate(
    *,
    trade: ProposedTrade,
    snapshot: RiskSnapshot,
    config: RiskConfig,
    profile: VenueProfile,
    step_size: Decimal,
) -> Evaluation:
    """Pure: no I/O, no clock, no randomness. Two calls with the same inputs cannot disagree."""
    ctx = build_context(
        trade=trade, snapshot=snapshot, config=config, profile=profile, step_size=step_size
    )
    results = tuple(RULES[rule_id](ctx) for rule_id in RULE_ORDER)
    primary = next((result.rule_id for result in results if not result.passed), None)
    return Evaluation(
        rule_results=results,
        primary_rule_id=primary,
        sizing=ctx.sizing,
        rr_net=ctx.rr_net,
        input_hash=input_hash(trade=trade, snapshot=snapshot, config=config, profile=profile),
    )
