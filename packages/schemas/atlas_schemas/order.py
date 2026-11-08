"""Order-path models: the only object execution accepts (contracts/decision-risk.md §5)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

OrderStatus = Literal[
    "PENDING_NEW",
    "OPEN",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELED",
    "REJECTED",
    "EXPIRED",
    "UNKNOWN",
]

# Stable refusal strings. The UI and the tests assert these verbatim.
VerdictRefusal = Literal[
    "VERDICT_EXPIRED",
    "VERDICT_CONSUMED",
    "VERDICT_SIGNATURE_INVALID",
    "VERDICT_NOT_APPROVED",
    "VERDICT_PAYLOAD_MISMATCH",
    "VERDICT_UNKNOWN_ID",
    "MODE_NOT_TRADABLE",
    "COST_MODEL_INCOMPLETE",
]

REFUSALS: frozenset[str] = frozenset(VerdictRefusal.__args__)  # type: ignore[attr-defined]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SignedOrder(_Model):
    signed_order_id: UUID
    verdict_id: UUID
    proposal_id: UUID
    venue_id: str
    pair: str
    side: Literal["BUY", "SELL"]
    type: Literal["LIMIT", "MARKET"]
    quantity: Decimal = Field(gt=0)
    limit_price: Decimal | None = None
    stop_price: Decimal = Field(gt=0)
    client_order_id: str = Field(min_length=1)
    signature: str = Field(min_length=64, max_length=64)
    signed_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def _expiry(self) -> SignedOrder:
        if self.expires_at <= self.signed_at:
            raise ValueError("expires_at must be after signed_at")
        if self.type == "LIMIT" and self.limit_price is None:
            raise ValueError("LIMIT order requires limit_price")
        return self


class ProtectiveStopRequest(_Model):
    """A reduce-only stop placement/tightening, independent of a new verdict (FR-063)."""

    request_id: UUID
    position_id: UUID
    venue_id: str
    pair: str
    quantity: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    generation: int = Field(ge=1)
    requested_at: datetime
    expires_at: datetime
    signature: str

    @model_validator(mode="after")
    def _expiry(self) -> ProtectiveStopRequest:
        if self.expires_at <= self.requested_at:
            raise ValueError("expires_at must be after requested_at")
        return self
