"""The rule catalogue: one pure function per rule id, evaluated in a fixed order.

``STAGES`` is the norm from contracts/decision-risk.md § "Evaluation order" — 22 ids in six
stages. The engine walks ``RULE_ORDER``, records every result, and reports the first failure
as ``primary_rule_id``, so an audit record diffs cleanly between versions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from atlas_schemas.risk import RuleResult
from atlas_schemas.venue import VenueProfile

from atlas_risk.costs import Sizing
from atlas_risk.models import ProposedTrade, RiskConfig, RiskSnapshot


@dataclass(frozen=True, slots=True)
class RuleContext:
    """Everything a rule may read. Sizing and the net reward ratio are computed once."""

    snapshot: RiskSnapshot
    trade: ProposedTrade
    config: RiskConfig
    profile: VenueProfile
    sizing: Sizing | None = None
    rr_net: Decimal | None = None


Rule = Callable[[RuleContext], RuleResult]

STAGES: tuple[tuple[str, ...], ...] = (
    ("MARKET_NOT_SPOT", "VENUE_NOT_LIVE_ENABLED"),
    (
        "RECONCILIATION_MISMATCH",
        "STALE_PRICE",
        "SPREAD_BREAKER",
        "VOLATILITY_BREAKER",
        "MACRO_BLACKOUT",
        "NEWS_ONLY_EVIDENCE",
    ),
    ("NO_STOP_LOSS", "STOP_TOO_WIDE", "RR_BELOW_MIN"),
    ("MIN_ORDER_EXCEEDS_RISK_BUDGET", "POSITION_SIZE_EXCEEDS_MAX"),
    (
        "ASSET_EXPOSURE_EXCEEDS_MAX",
        "TOTAL_EXPOSURE_EXCEEDS_MAX",
        "DAILY_LOSS_LIMIT",
        "MAX_DRAWDOWN",
        "MAX_OPEN_POSITIONS",
    ),
    ("ORDER_RATE_LIMIT", "COOLDOWN_ACTIVE", "DUPLICATE_PROPOSAL", "BUDGET_BLOCKED"),
)

RULE_ORDER: tuple[str, ...] = tuple(rule_id for stage in STAGES for rule_id in stage)

STAGE_OF: dict[str, int] = {
    rule_id: index + 1 for index, stage in enumerate(STAGES) for rule_id in stage
}


def _catalogue() -> dict[str, Rule]:
    from atlas_risk.rules import (
        evidence,
        freshness,
        operational,
        portfolio,
        sizing,
        structure,
        venue,
    )

    return {
        "MARKET_NOT_SPOT": venue.market_not_spot,
        "VENUE_NOT_LIVE_ENABLED": venue.venue_not_live_enabled,
        "RECONCILIATION_MISMATCH": freshness.reconciliation_mismatch,
        "STALE_PRICE": freshness.stale_price,
        "SPREAD_BREAKER": freshness.spread_breaker,
        "VOLATILITY_BREAKER": freshness.volatility_breaker,
        "MACRO_BLACKOUT": freshness.macro_blackout,
        "NEWS_ONLY_EVIDENCE": evidence.news_only_evidence,
        "NO_STOP_LOSS": structure.no_stop_loss,
        "STOP_TOO_WIDE": structure.stop_too_wide,
        "RR_BELOW_MIN": structure.rr_below_min,
        "MIN_ORDER_EXCEEDS_RISK_BUDGET": sizing.min_order_exceeds_risk_budget,
        "POSITION_SIZE_EXCEEDS_MAX": sizing.position_size_exceeds_max,
        "ASSET_EXPOSURE_EXCEEDS_MAX": portfolio.asset_exposure_exceeds_max,
        "TOTAL_EXPOSURE_EXCEEDS_MAX": portfolio.total_exposure_exceeds_max,
        "DAILY_LOSS_LIMIT": portfolio.daily_loss_limit,
        "MAX_DRAWDOWN": portfolio.max_drawdown,
        "MAX_OPEN_POSITIONS": portfolio.max_open_positions,
        "ORDER_RATE_LIMIT": operational.order_rate_limit,
        "COOLDOWN_ACTIVE": operational.cooldown_active,
        "DUPLICATE_PROPOSAL": operational.duplicate_proposal,
        "BUDGET_BLOCKED": operational.budget_blocked,
    }


RULES: dict[str, Rule] = _catalogue()

__all__ = ["RULES", "RULE_ORDER", "STAGES", "STAGE_OF", "Rule", "RuleContext", "RuleResult"]
