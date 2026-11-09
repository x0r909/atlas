"""Turning an evaluation into the one object execution will accept: a signed RiskVerdict."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from atlas_schemas.risk import CostModelSource, RiskVerdict
from atlas_schemas.venue import VenueProfile
from atlas_schemas.verdict_sign import sign_verdict

from atlas_risk.engine import Evaluation
from atlas_risk.models import ProposedTrade, RiskConfig, RiskSnapshot

VERDICT_TTL_SECONDS = 60


class CostModelIncomplete(RuntimeError):
    """A live verdict may not rest on an assumed fee or tax rate (refusal COST_MODEL_INCOMPLETE)."""


def _signing_payload(verdict: RiskVerdict, trade: ProposedTrade) -> dict[str, Any]:
    """VERDICT_FIELDS spans both objects: trade supplies pair/side/prices, verdict the rest."""
    return {
        "proposal_id": trade.proposal_id,
        "venue_id": trade.venue_id,
        "pair": trade.pair,
        "side": trade.side,
        "sized_quantity": verdict.sized_quantity,
        "stop_price": trade.stop_price,
        "target_price": trade.target_price,
        "verdict_at": verdict.verdict_at,
        "expires_at": verdict.expires_at,
    }


def build(
    *,
    evaluation: Evaluation,
    trade: ProposedTrade,
    snapshot: RiskSnapshot,
    config: RiskConfig,
    profile: VenueProfile,
    verdict_at: datetime,
    signing_key: bytes,
    portfolio_snapshot_id: UUID,
    venue_profile_id: int,
    extra_rule_results: tuple[Any, ...] = (),
) -> RiskVerdict:
    if snapshot.require_live_venue and not profile.cost_model_complete():
        raise CostModelIncomplete(
            f"{profile.venue_id} profile v{profile.cost_model_version} carries an assumed rate"
        )

    source: CostModelSource = "VERIFIED" if profile.cost_model_complete() else "ASSUMED"
    sizing = evaluation.sizing
    approved = evaluation.approved and sizing is not None

    verdict = RiskVerdict(
        verdict_id=uuid4(),
        proposal_id=trade.proposal_id,
        approved=approved,
        rule_results=list(evaluation.rule_results) + list(extra_rule_results),
        primary_rule_id=evaluation.primary_rule_id,
        sized_quantity=sizing.quantity if sizing else None,
        sized_notional_idr=sizing.notional_idr if sizing else None,
        realised_risk_idr=sizing.realised_risk_idr if sizing else None,
        realised_risk_pct=sizing.realised_risk_pct if sizing else None,
        rr_net=evaluation.rr_net,
        cost_breakdown=sizing.costs if sizing else None,
        cost_model_source=source,
        risk_config_version=config.version,
        venue_profile_id=venue_profile_id,
        portfolio_snapshot_id=portfolio_snapshot_id,
        input_hash=evaluation.input_hash,
        verdict_at=verdict_at,
        expires_at=verdict_at + timedelta(seconds=VERDICT_TTL_SECONDS),
        signature="0" * 64,
    )
    if not approved:
        return verdict.model_copy(update={"signature": None})

    signature = sign_verdict(_signing_payload(verdict, trade), signing_key)
    return verdict.model_copy(update={"signature": signature})
