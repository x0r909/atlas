# Contract: Decision → Risk → Execution

**Owner**: `packages/schemas` (models), `services/risk-engine` (verdict + signing)
**Consumers**: `services/agents`, `services/execution`, `apps/terminal-web`, `services/backtester`

The single most important contract in ATLAS. The LLM may only produce `TradeProposal` or
`ManagementAction`. Only the Risk Engine may produce a `RiskVerdict`, and only an approved
verdict can be turned into a `SignedOrder`, which is the only input `execution` accepts.

## 1. Model output envelope

The model always answers with one JSON object:

```json
{
  "schema_version": "1.0",
  "decision": "PROPOSE_TRADE | MANAGE_POSITION | NO_TRADE",
  "trade": { "...": "TradeProposal or null" },
  "management": { "...": "ManagementAction or null" },
  "reasoning_summary": "<= 400 chars, Indonesian or English",
  "confidence": 0.0
}
```

Parsing rules: `extra="forbid"`; on any validation error the cycle records
`INVALID_OUTPUT`, emits no proposal, and the mode is unchanged. A `NO_TRADE` with a valid
envelope is a normal outcome and counts toward cycle metrics.

## 2. `TradeProposal`

```python
class TradeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    proposal_id: UUID
    cycle_id: UUID
    venue_id: str
    pair: str
    side: Literal["BUY"]                 # spot, long-only
    entry_type: Literal["LIMIT", "MARKET_LIMIT"]
    entry_price: Decimal
    stop_price: Decimal                  # MANDATORY
    target_price: Decimal
    horizon_hours: int                   # 4..120
    intended_notional_idr: Decimal       # suggestion only; Risk Engine resizes
    confidence: Decimal                  # 0..1
    rationale_summary: str               # <= 400 chars, untrusted text
    evidence: list[EvidenceRef]           # typed; tier-3-only evidence cannot open a position
```

```python
class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["BAR", "NEWS", "SOCIAL"]
    ref_id: UUID                         # bar window id, news_id, or social item id
    source_tier: Literal[1, 2, 3] | None # required for NEWS/SOCIAL, null for BAR
```

Evidence is typed rather than a bare id list because `NEWS_ONLY_EVIDENCE` must be decidable
from the proposal alone — resolving an opaque UUID against the database would make the rule
impure and untestable.

Invariants (validated before the Risk Engine sees it):
`stop_price < entry_price < target_price`; `horizon_hours ∈ [4,120]`;
all prices are multiples of `tick_size`; `intended_notional_idr ≥ min_notional`;
`venue_id` present and enabled for live; `rationale_summary` length ≤ 400.

Allowed actions per mode: `PAPER` and `CO_PILOT` accept proposals; `HALTED` and `BACKTEST`
reject all new proposals (management of open positions still allowed in `HALTED`).

## 3. `ManagementAction`

```python
class ManagementAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action_id: UUID
    position_id: UUID
    type: Literal["MOVE_STOP", "CLOSE", "TIME_STOP"]
    new_stop_price: Decimal | None       # required for MOVE_STOP, must tighten only
    reason: str
```

`MOVE_STOP` may only move the stop in the profitable direction (never widen). `CLOSE` closes the
position in full and `TIME_STOP` is the scheduler's full close at the maximum holding time; both
may be issued by the deterministic scheduler without an agent run. Scale in, scale out, and
partial take profit are not valid actions in v1 and MUST be rejected by the schema (FR-020).

## 4. `RiskVerdict`

```python
class RuleResult(BaseModel):
    rule_id: str
    passed: bool
    observed: str | None
    limit: str | None
    message: str

class CostBreakdown(BaseModel):
    entry_fee_idr: Decimal
    exit_fee_idr: Decimal
    cfx_idr: Decimal
    tax_pph_idr: Decimal
    tax_ppn_idr: Decimal
    slippage_allowance_idr: Decimal
    total_round_trip_idr: Decimal

class RiskVerdict(BaseModel):
    verdict_id: UUID
    proposal_id: UUID
    approved: bool
    rule_results: list[RuleResult]        # every evaluated rule, in fixed order
    primary_rule_id: str | None           # first failing rule; null if approved
    sized_quantity: Decimal | None
    sized_notional_idr: Decimal | None
    realised_risk_idr: Decimal | None
    realised_risk_pct: Decimal | None
    rr_net: Decimal | None
    cost_breakdown: CostBreakdown | None
    risk_config_version: int
    venue_profile_id: int
    portfolio_snapshot_id: UUID
    input_hash: str                       # sha256 of canonical snapshot
    verdict_at: datetime
    expires_at: datetime                  # verdict_at + 60s, immutable
    signature: str | None                 # hex HMAC-SHA256; null when approved is False
    consumed_at: datetime | None          # set exactly once by execution
```

### Verdict lifetime and consumption (FR-062)

An approved verdict is a single-use coupon, not a standing permission:

- The signature covers the canonical verdict payload, which includes `proposal_id`,
  `venue_id`, `pair`, `side`, `sized_quantity`, `stop_price`, and `target_price`. Changing
  any of those invalidates the signature.
- `expires_at` is exactly 60 s after `verdict_at`. A verdict past it can never produce an
  order, even if the process that holds it is still alive.
- `consumed_at` is written in the same transaction as the `order` insert. A second
  submission for the same `verdict_id` — replay, double click, retried task, duplicate
  consumer — is refused and audited as `VERDICT_ALREADY_CONSUMED`.
- Refusal reasons are stable strings so tests and the UI can assert them:
  `VERDICT_EXPIRED`, `VERDICT_CONSUMED`, `VERDICT_SIGNATURE_INVALID`,
  `VERDICT_NOT_APPROVED`, `VERDICT_PAYLOAD_MISMATCH`, `VERDICT_UNKNOWN_ID`,
  `MODE_NOT_TRADABLE`.
- Exits do not wait for a coupon: closing a position, placing or tightening its protective
  stop, and cancelling an unfilled entry are reduce-only and proceed without a new verdict
  (FR-063). The refusal of those paths is limited to signature/lifetime of the order they
  carry, never to mode, breaker, spend cap, or an `UNKNOWN` freeze.

### Mode gate at the execution boundary (FR-001, FR-002)

A valid verdict is necessary but not sufficient: the mode is read from `mode_state` inside
the same transaction that consumes the verdict, so a mode change cannot race a submission.

| Mode | Entry order (opens/increases) | Reduce-only (close, protective stop, entry cancel) |
| --- | --- | --- |
| `PAPER` | allowed **only** on a simulated or `PAPER`-enabled venue; on a live-enabled venue refused with `MODE_NOT_TRADABLE` | allowed |
| `CO_PILOT`, `AUTONOMOUS` | allowed | allowed |
| `HALTED` | refused with `MODE_NOT_TRADABLE` | allowed (FR-063) |
| `BACKTEST` | refused — no live submission exists | n/a |
| unreadable / unknown | refused with `MODE_NOT_TRADABLE`; never defaults to a tradable live mode | allowed |

`MODE_NOT_TRADABLE` is checked before any venue call, writes one `audit_record`, and is
distinct from a risk-rule rejection: no rule ran, so `rule_results` is empty. Reduce-only is
computed from stored position state, never from the caller's claim (FR-071).

### Evaluation order (fixed, short-circuit on first failure but still report all rules)

1. `MARKET_NOT_SPOT`, `VENUE_NOT_LIVE_ENABLED`
2. `RECONCILIATION_MISMATCH`, `STALE_PRICE`, `SPREAD_BREAKER`, `VOLATILITY_BREAKER`,
   `MACRO_BLACKOUT`, `NEWS_ONLY_EVIDENCE`
3. `NO_STOP_LOSS`, `STOP_TOO_WIDE`, `RR_BELOW_MIN`
4. sizing: `MIN_ORDER_EXCEEDS_RISK_BUDGET`, `POSITION_SIZE_EXCEEDS_MAX`
5. portfolio: `ASSET_EXPOSURE_EXCEEDS_MAX`, `TOTAL_EXPOSURE_EXCEEDS_MAX`,
   `DAILY_LOSS_LIMIT`, `MAX_DRAWDOWN`, `MAX_OPEN_POSITIONS`
6. operational: `ORDER_RATE_LIMIT`, `COOLDOWN_ACTIVE`, `DUPLICATE_PROPOSAL`,
   `BUDGET_BLOCKED`

Every rule is a pure function `(snapshot, proposal, config, profile) -> RuleResult`.
`rule_results` order is stable and asserted by a golden test, so the audit record diffs
cleanly across versions. The six stages hold 22 evaluated rule ids; `INVALID_OUTPUT` is the
envelope-level rejection (section 1) and never appears in `rule_results`, so the contract
documents 23 ids in total.

### Rule: `NEWS_ONLY_EVIDENCE` (FR-013)

Rejects a proposal whose supporting evidence is **only** unverified material:

| Evidence set | Outcome |
| --- | --- |
| one or more `BAR` items | passes |
| at least one `NEWS`/`SOCIAL` item with `source_tier` 1 or 2 | passes |
| only `NEWS`/`SOCIAL` items, all `source_tier == 3` | rejects `NEWS_ONLY_EVIDENCE` |
| empty evidence list | rejects `NEWS_ONLY_EVIDENCE` (an unexplained trade is not a researched one) |

Tier-3 material is still allowed as *support* beside technical or tier-1/2 evidence; the rule
forbids it as the sole justification. `observed` names the highest tier present and the
`BAR` count, so the audit record shows why the rule fired.

### Sizing formula (normative)

```python
risk_budget_idr   = equity_idr * risk_per_trade_pct / 100
stop_distance_pct = (entry_price - stop_price) / entry_price * 100
raw_qty           = risk_budget_idr / (entry_price - stop_price)
qty               = floor_to_step(raw_qty, step_size)
notional          = qty * entry_price
realised_risk     = qty * (entry_price - stop_price) + round_trip_costs
if notional < venue.min_notional:  reject MIN_ORDER_EXCEEDS_RISK_BUDGET
if notional > equity * 25%:        qty = floor_to_step(...) or reject POSITION_SIZE_EXCEEDS_MAX
```

Rounding is always **down**; the verdict reports the realised risk after rounding.
`rr_net = (target - entry - cost_per_unit) / (entry - stop + cost_per_unit)` where
`cost_per_unit` is the full round-trip cost (fees + CFX + PPh + PPN + slippage) per unit.

### Stop not confirmed after a fill (FR-033, FR-063, FR-071)

An entry fill does not become a protected position until a stop is confirmed. The Execution
Engine tracks `position.stop_confirmation = CONFIRMED | UNCONFIRMED`:

- the stop order is rejected, times out, or is never acknowledged ⇒ `UNCONFIRMED`;
- `UNCONFIRMED` starts the immediate reduce-only close path in the same cycle, writes one
  `SAFETY` `audit_record`, raises incident `STOP_UNCONFIRMED`, and opens a Telegram alert;
- the close is reduce-only, so `HALTED`, an active breaker, and an exhausted LLM budget cannot
  block it — the same exemption the mode gate grants (FR-071);
- a close that itself fails follows the FR-070 escalation ladder (retry, alert at the second
  failure, bounded market fallback, `HALTED` when flat cannot be reached);
- entries stay blocked until the position is flat, so an unconfirmed stop cannot be doubled
  down on.

`STOP_UNCONFIRMED` is an incident id, not a risk rule: no verdict was involved and
`rule_results` is empty. It is distinct from `NO_STOP_LOSS`, which rejects a *proposal* that
never carried a stop price.

## 5. `SignedOrder`

```python
class SignedOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    signed_order_id: UUID
    verdict_id: UUID                     # unique
    proposal_id: UUID
    venue_id: str
    pair: str
    side: Literal["BUY", "SELL"]
    type: Literal["LIMIT", "MARKET"]
    quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal
    client_order_id: str
    signature: str                       # hex HMAC-SHA256
    signed_at: datetime
    expires_at: datetime                 # signed_at + 60s
```

Signing: `HMAC-SHA256(key=atlas_order_hmac_key, msg=canonical_json(payload) + verdict_id)`.
The HMAC key exists only in `execution`'s environment and in `risk-engine`'s environment.
Neither `agents` nor `api-gateway` can read it, so a compromised agent process cannot forge
an order.

`execution` rejects (with a recorded reason, no order placed) when: signature invalid,
`expires_at` passed, verdict not `approved`, that `verdict_id` was already consumed, more
than one order already exists for that verdict, `venue_id` disabled, the mode is `PAPER` on a
live-enabled venue or `HALTED` for an entry (`MODE_NOT_TRADABLE`), the envelope's `producer`
is not `risk-engine` (see `contracts/event-bus.md` § Producer allowlist), or the order's
pair/price/quantity fails the venue's current `PairInfo` (re-checked at submit time,
because limits can change between proposal and submission).

`execution` is the **single writer** for order mutations (FR-071): place, cancel, and
modify all pass through it and are audited, and no other service holds a client able to
call a venue order endpoint. Cancel or modify of an unfilled entry order requires the
order's originating verdict to still be valid; cancelling a reduce-only order is always
attempted, and its rejection by the venue is recorded as an incident rather than ignored.

## 6. Replay contract (Principle VII / FR-039)

Given a `decision_record`:

```python
snapshot = {market, news, portfolio, order_book, config, venue_profile}
verdict_replayed = risk_engine.evaluate(proposal_replayed, snapshot)
assert verdict_replayed.approved == recorded.approved
assert verdict_replayed.primary_rule_id == recorded.primary_rule_id
assert verdict_replayed.rule_results == recorded.rule_results
assert signed_order_payload_replayed == recorded.order_payload
```

The replay must be byte-identical for the verdict and order payload; only timestamps differ.
A nightly job replays the last 20 decisions and opens a `SAFETY` incident on any divergence.
