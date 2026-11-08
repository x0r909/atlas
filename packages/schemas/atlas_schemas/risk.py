"""Decision-side models: what an agent may emit and what the Risk Engine may sign.

contracts/decision-risk.md is the normative source; this module is its executable form.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

DecisionKind = Literal["PROPOSE_TRADE", "MANAGE_POSITION", "NO_TRADE"]
CostModelSource = Literal["VERIFIED", "ASSUMED"]


class _Model(BaseModel):
    """Frozen and closed: an unknown field is a contract break, not a hint."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceRef(_Model):
    kind: Literal["BAR", "NEWS", "SOCIAL"]
    ref_id: UUID
    source_tier: Literal[1, 2, 3] | None = None

    @model_validator(mode="after")
    def _tier_rules(self) -> EvidenceRef:
        if self.kind == "BAR" and self.source_tier is not None:
            raise ValueError("BAR evidence carries no source tier")
        if self.kind != "BAR" and self.source_tier is None:
            raise ValueError("NEWS/SOCIAL evidence requires source_tier")
        return self


class TradeProposal(_Model):
    proposal_id: UUID
    cycle_id: UUID
    venue_id: str
    pair: str
    side: Literal["BUY"]
    entry_type: Literal["LIMIT", "MARKET_LIMIT"]
    entry_price: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    target_price: Decimal = Field(gt=0)
    horizon_hours: int = Field(ge=4, le=120)
    intended_notional_idr: Decimal = Field(gt=0)
    confidence: Decimal = Field(ge=0, le=1)
    rationale_summary: str = Field(max_length=400)
    evidence: list[EvidenceRef]

    @model_validator(mode="after")
    def _price_order(self) -> TradeProposal:
        if not (self.stop_price < self.entry_price < self.target_price):
            raise ValueError("BUY requires stop_price < entry_price < target_price")
        return self


class ManagementAction(_Model):
    action_id: UUID
    position_id: UUID
    type: Literal["MOVE_STOP", "CLOSE", "TIME_STOP"]
    new_stop_price: Decimal | None = None
    reason: str

    @model_validator(mode="after")
    def _stop_required(self) -> ManagementAction:
        if self.type == "MOVE_STOP" and self.new_stop_price is None:
            raise ValueError("MOVE_STOP requires new_stop_price")
        if self.type != "MOVE_STOP" and self.new_stop_price is not None:
            raise ValueError("only MOVE_STOP carries new_stop_price")
        return self


class ModelDecisionEnvelope(_Model):
    """The only object the model may answer with."""

    schema_version: Literal["1.0"]
    decision: DecisionKind
    trade: TradeProposal | None = None
    management: ManagementAction | None = None
    reasoning_summary: str = Field(max_length=400)
    confidence: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _consistent(self) -> ModelDecisionEnvelope:
        if self.decision == "PROPOSE_TRADE" and self.trade is None:
            raise ValueError("PROPOSE_TRADE requires trade")
        if self.decision == "MANAGE_POSITION" and self.management is None:
            raise ValueError("MANAGE_POSITION requires management")
        if self.decision == "NO_TRADE" and (self.trade or self.management):
            raise ValueError("NO_TRADE carries no trade or management action")
        return self


class RuleResult(_Model):
    rule_id: str
    passed: bool
    observed: str | None = None
    limit: str | None = None
    message: str = ""
    cost_model_source: CostModelSource | None = None


class CostBreakdown(_Model):
    entry_fee_idr: Decimal
    exit_fee_idr: Decimal
    cfx_idr: Decimal
    tax_pph_idr: Decimal
    tax_ppn_idr: Decimal
    slippage_allowance_idr: Decimal
    total_round_trip_idr: Decimal


class RiskVerdict(_Model):
    verdict_id: UUID
    proposal_id: UUID
    approved: bool
    rule_results: list[RuleResult]
    primary_rule_id: str | None = None
    sized_quantity: Decimal | None = None
    sized_notional_idr: Decimal | None = None
    realised_risk_idr: Decimal | None = None
    realised_risk_pct: Decimal | None = None
    rr_net: Decimal | None = None
    cost_breakdown: CostBreakdown | None = None
    cost_model_source: CostModelSource | None = None
    risk_config_version: int
    venue_profile_id: int
    portfolio_snapshot_id: UUID
    input_hash: str
    verdict_at: datetime
    expires_at: datetime
    signature: str | None = None
    consumed_at: datetime | None = None

    @model_validator(mode="after")
    def _approval_consistency(self) -> RiskVerdict:
        if self.approved and self.signature is None:
            raise ValueError("an approved verdict must be signed")
        if not self.approved and self.signature is not None:
            raise ValueError("a rejected verdict carries no signature")
        if self.expires_at <= self.verdict_at:
            raise ValueError("expires_at must be after verdict_at")
        if self.approved and self.primary_rule_id is not None:
            raise ValueError("an approved verdict has no primary failing rule")
        return self
