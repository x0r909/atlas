/**
 * GENERATED FILE - do not edit.
 * Source: packages/schemas/atlas_schemas (pydantic models).
 * Regenerate with `make types`; `make types-check` fails when this file is stale.
 * A drifted field here means the terminal renders a shape the backend never sends.
 */

export interface Balance {
  asset: string;
  free: string;
  locked: string;
}

export interface Bar {
  pair: string;
  timeframe: string;
  ts: number;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  trade_count?: number | null;
}

export interface CostBreakdown {
  entry_fee_idr: string;
  exit_fee_idr: string;
  cfx_idr: string;
  tax_pph_idr: string;
  tax_ppn_idr: string;
  slippage_allowance_idr: string;
  total_round_trip_idr: string;
}

export interface EventEnvelope {
  event_id: string;
  event_type: string;
  schema_version?: number;
  produced_at: number;
  producer: string;
  correlation_id: string;
  causation_id?: string | null;
  venue_id?: string | null;
  payload?: Record<string, unknown>;
}

export interface EvidenceRef {
  kind: "BAR" | "NEWS" | "SOCIAL";
  ref_id: string;
  source_tier?: unknown | unknown | unknown | null;
}

export interface ManagementAction {
  action_id: string;
  position_id: string;
  type: "MOVE_STOP" | "CLOSE" | "TIME_STOP";
  new_stop_price?: string | null;
  reason: string;
}

export interface MarketEvent {
  kind: "trade" | "ticker" | "book" | "heartbeat_missed";
  pair: string;
  venue_ts: number;
  trade?: PublicTrade | null;
  ticker?: Ticker | null;
  book?: OrderBook | null;
}

export interface ModelDecisionEnvelope {
  schema_version: "1.0";
  decision: "PROPOSE_TRADE" | "MANAGE_POSITION" | "NO_TRADE";
  trade?: TradeProposal | null;
  management?: ManagementAction | null;
  reasoning_summary: string;
  confidence: string;
}

export interface OrderBook {
  pair: string;
  bids: [string, string][];
  asks: [string, string][];
  venue_ts: number;
  checksum?: string | null;
}

export interface PairInfo {
  pair: string;
  base: string;
  quote: string;
  tick_size: string;
  step_size: string;
  min_notional: string;
  min_qty: string;
  enabled: boolean;
}

export interface PlaceOrderRequest {
  pair: string;
  side: "BUY" | "SELL";
  type: "LIMIT" | "MARKET";
  quantity: string;
  limit_price?: string | null;
  client_order_id: string;
  signed_order_id: string;
}

export interface ProtectiveStopRequest {
  request_id: string;
  position_id: string;
  venue_id: string;
  pair: string;
  quantity: string;
  stop_price: string;
  generation: number;
  requested_at: string;
  expires_at: string;
  signature: string;
}

export interface PublicTrade {
  pair: string;
  trade_id: string;
  price: string;
  quantity: string;
  side: "BUY" | "SELL";
  venue_ts: number;
}

export interface RiskVerdict {
  verdict_id: string;
  proposal_id: string;
  approved: boolean;
  rule_results: RuleResult[];
  primary_rule_id?: string | null;
  sized_quantity?: string | null;
  sized_notional_idr?: string | null;
  realised_risk_idr?: string | null;
  realised_risk_pct?: string | null;
  rr_net?: string | null;
  cost_breakdown?: CostBreakdown | null;
  cost_model_source?: "VERIFIED" | "ASSUMED" | null;
  risk_config_version: number;
  venue_profile_id: number;
  portfolio_snapshot_id: string;
  input_hash: string;
  verdict_at: string;
  expires_at: string;
  signature?: string | null;
  consumed_at?: string | null;
}

export interface RuleResult {
  rule_id: string;
  passed: boolean;
  observed?: string | null;
  limit?: string | null;
  message?: string;
  cost_model_source?: "VERIFIED" | "ASSUMED" | null;
}

export interface SignedOrder {
  signed_order_id: string;
  verdict_id: string;
  proposal_id: string;
  venue_id: string;
  pair: string;
  side: "BUY" | "SELL";
  type: "LIMIT" | "MARKET";
  quantity: string;
  limit_price?: string | null;
  stop_price: string;
  client_order_id: string;
  signature: string;
  signed_at: string;
  expires_at: string;
}

export interface Ticker {
  pair: string;
  bid: string;
  ask: string;
  bid_size: string;
  ask_size: string;
  last: string;
  venue_ts: number;
  ingested_at: number;
}

export interface TradeProposal {
  proposal_id: string;
  cycle_id: string;
  venue_id: string;
  pair: string;
  side: "BUY";
  entry_type: "LIMIT" | "MARKET_LIMIT";
  entry_price: string;
  stop_price: string;
  target_price: string;
  horizon_hours: number;
  intended_notional_idr: string;
  confidence: string;
  rationale_summary: string;
  evidence: EvidenceRef[];
}

export interface VenueCapabilities {
  venue_id: string;
  quote_currencies: string[];
  markets: "SPOT"[];
  supports_ws_public: boolean;
  supports_ws_private: boolean;
  supports_client_order_id: boolean;
  self_trade_prevention: boolean;
  native_stop_orders: boolean;
  supports_order_modify: boolean;
  max_leverage: unknown;
  regulator: "OJK" | "BAPPEBTI_LEGACY" | "NONE";
}

export interface VenueOrder {
  pair: string;
  venue_order_id?: string | null;
  client_order_id?: string | null;
  side: "BUY" | "SELL";
  type: "LIMIT" | "MARKET";
  quantity: string;
  limit_price?: string | null;
  filled_quantity?: string;
  remaining_quantity?: string | null;
  status: "PENDING_NEW" | "OPEN" | "PARTIALLY_FILLED" | "FILLED" | "CANCELED" | "REJECTED" | "EXPIRED" | "UNKNOWN";
  avg_fill_price?: string | null;
  venue_ts: number;
  reject_reason?: string | null;
}

export interface VenueOrderAck {
  pair: string;
  venue_order_id: string | null;
  client_order_id: string;
  status: "PENDING_NEW" | "OPEN" | "PARTIALLY_FILLED" | "FILLED" | "CANCELED" | "REJECTED" | "EXPIRED" | "UNKNOWN";
  venue_ts: number;
  code?: string | null;
  message?: string | null;
}

export interface VenueProfile {
  venue_id: string;
  version: number;
  fee_maker_pct: string;
  fee_taker_pct: string;
  cfx_fee_pct?: string | null;
  cfx_fee_pct_assumed: string;
  tax_buy_pct_assumed: string;
  tax_pph_pct: string;
  tax_ppn_pct: string;
  tax_applies_to: "SELL_ONLY" | "BOTH_SIDES";
  min_notional_idr: string;
  max_leverage: unknown;
  native_stop_orders: boolean;
  cost_model_version: string;
}

export interface VenueTrade {
  pair: string;
  venue_trade_id: string;
  order_id?: string | null;
  side: "BUY" | "SELL";
  quantity: string;
  price: string;
  fee_idr?: string;
  tax_idr?: string;
  is_maker?: boolean | null;
}
