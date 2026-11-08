"""Shared pydantic v2 models for every ATLAS service boundary.

One import surface for consumers: ``from atlas_schemas import TradeProposal, RiskVerdict``.
"""

from __future__ import annotations

from atlas_schemas.events import SCHEMA_VERSION, EventEnvelope, new_event_id
from atlas_schemas.order import (
    REFUSALS,
    OrderStatus,
    ProtectiveStopRequest,
    SignedOrder,
    VerdictRefusal,
)
from atlas_schemas.risk import (
    CostBreakdown,
    CostModelSource,
    DecisionKind,
    EvidenceRef,
    ManagementAction,
    ModelDecisionEnvelope,
    RiskVerdict,
    RuleResult,
    TradeProposal,
)
from atlas_schemas.venue import (
    Balance,
    Bar,
    MarketEvent,
    OrderBook,
    PairInfo,
    PlaceOrderRequest,
    PublicTrade,
    Ticker,
    VenueAuthError,
    VenueCapabilities,
    VenueError,
    VenueNotFound,
    VenueOrder,
    VenueOrderAck,
    VenueProfile,
    VenueRateLimited,
    VenueRejected,
    VenueSchemaError,
    VenueTrade,
    VenueUnavailable,
)

__all__ = [
    "REFUSALS",
    "SCHEMA_VERSION",
    "Balance",
    "Bar",
    "CostBreakdown",
    "CostModelSource",
    "DecisionKind",
    "EventEnvelope",
    "EvidenceRef",
    "ManagementAction",
    "MarketEvent",
    "ModelDecisionEnvelope",
    "OrderBook",
    "OrderStatus",
    "PairInfo",
    "PlaceOrderRequest",
    "ProtectiveStopRequest",
    "PublicTrade",
    "RiskVerdict",
    "RuleResult",
    "SignedOrder",
    "Ticker",
    "TradeProposal",
    "VenueAuthError",
    "VenueCapabilities",
    "VenueError",
    "VenueNotFound",
    "VenueOrder",
    "VenueOrderAck",
    "VenueProfile",
    "VenueRateLimited",
    "VenueRejected",
    "VenueSchemaError",
    "VenueTrade",
    "VenueUnavailable",
    "VerdictRefusal",
    "new_event_id",
]
