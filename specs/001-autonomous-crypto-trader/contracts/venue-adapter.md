# Contract: Venue Adapter Port

**Owner**: `packages/schemas` (types) + each adapter implementation
**Consumers**: `ingest-market`, `execution`, `backtester` (via `SimulatedVenue`)

Every venue speaks exactly this port. No service outside the adapter may know an exchange's
payload shape, rate-limit rules, fee formula, or minimum order size. Adding a venue MUST be
a new adapter module plus a `venue` / `venue_profile` row — no change to the Risk Engine.

## Capability declaration

```python
class VenueCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    venue_id: str
    quote_currencies: list[str]            # ["IDR"]
    markets: list[Literal["SPOT"]]         # futures/derivatives are NOT implemented
    supports_ws_public: bool
    supports_ws_private: bool
    supports_client_order_id: bool         # must be True for live
    self_trade_prevention: bool
    native_stop_orders: bool               # venue accepts a resting stop/bracket order
    supports_order_modify: bool            # amend without cancel+replace
    max_leverage: Literal[1]
    regulator: Literal["OJK", "BAPPEBTI_LEGACY", "NONE"]
```

`native_stop_orders = false` selects the client-side stop monitor (FR-067); it is a
capability flag, not a per-venue branch inside the Risk Engine. Indodax stays `false` until
M0 confirms stop-order support against the trade API (research.md open item).

`execution` refuses to start against a venue where `supports_client_order_id` is false:
idempotency is a hard requirement (constitution, Trading System Constraints).

Only `execution` may call the mutating half of this port (`place_order`, `cancel_order`,
`get_order` for state resolution). `ingest-market` may call the read-only market-data half,
and `backtester` calls only `SimulatedVenue`. The order port has no withdraw, deposit, or
transfer method at all (FR-064).

## Operations

```python
class VenuePort(Protocol):
    # --- metadata -------------------------------------------------------
    async def capabilities(self) -> VenueCapabilities: ...
    async def profile(self) -> VenueProfile: ...          # fees, taxes, min notional, steps
    async def pairs(self) -> list[PairInfo]: ...          # tick_size, step_size, min_notional
    async def server_time_ms(self) -> int: ...            # for clock-drift detection

    # --- market data (public) -------------------------------------------
    async def tickers(self, pairs: list[str] | None = None) -> list[Ticker]: ...
    async def order_book(self, pair: str, depth: int = 50) -> OrderBook: ...
    async def recent_trades(self, pair: str, limit: int = 500) -> list[PublicTrade]: ...
    async def bars(self, pair: str, timeframe: str, since_ms: int, limit: int) -> list[Bar]: ...
    def stream(self, pairs: list[str]) -> AsyncIterator[MarketEvent]: ...

    # --- account (private, trade-only keys) -----------------------------
    async def balances(self) -> list[Balance]: ...
    async def open_orders(self) -> list[VenueOrder]: ...
    async def order_history(self, pair: str | None, since_ms: int, limit: int) -> list[VenueOrder]: ...
    async def trades(self, pair: str | None, since_ms: int) -> list[VenueTrade]: ...

    # --- orders (private) -----------------------------------------------
    async def place_order(self, req: PlaceOrderRequest) -> VenueOrderAck: ...
    async def cancel_order(self, pair: str, venue_order_id: str) -> VenueOrderAck: ...
    async def modify_order(self, pair: str, venue_order_id: str, *,
                           quantity: Decimal | None = None,
                           limit_price: Decimal | None = None) -> VenueOrderAck: ...
    async def get_order(self, pair: str, *, venue_order_id: str | None = None,
                        client_order_id: str | None = None) -> VenueOrder: ...
```

There is **no** `withdraw`, `transfer`, `deposit`, or key-management method anywhere in the
port. An adapter that exposes one fails review (Principle VIII).

## Core types (in `packages/schemas/venue.py`)

```python
class PairInfo(BaseModel):
    pair: str                  # "btc_idr"
    base: str; quote: str
    tick_size: Decimal
    step_size: Decimal
    min_notional: Decimal      # quote currency, e.g. 10000 IDR
    min_qty: Decimal
    enabled: bool

class Ticker(BaseModel):
    pair: str
    bid: Decimal; ask: Decimal
    bid_size: Decimal; ask_size: Decimal
    last: Decimal
    venue_ts: int              # ms
    ingested_at: int           # ms, our clock

class OrderBook(BaseModel):
    pair: str
    bids: list[tuple[Decimal, Decimal]]   # price, qty (descending)
    asks: list[tuple[Decimal, Decimal]]   # ascending
    venue_ts: int
    checksum: str | None

class PlaceOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    pair: str
    side: Literal["BUY", "SELL"]
    type: Literal["LIMIT", "MARKET"]
    quantity: Decimal
    limit_price: Decimal | None          # required for LIMIT
    client_order_id: str                 # required, deterministic from verdict
    signed_order_id: str                 # for audit correlation only
```

## Error taxonomy

Adapters never raise vendor exceptions upward. Every failure maps to:

| Exception | Meaning | Caller behaviour |
| --- | --- | --- |
| `VenueUnavailable` | network/5xx/timeout, **no evidence** the request was applied | retry with backoff ≤ 3, then `UNKNOWN` order state |
| `VenueRateLimited(retry_after_s)` | 429 / documented throttle | sleep, queue, never busy-loop |
| `VenueAuthError` | bad signature, permissions, IP | HARD halt live mode, incident SEV1 |
| `VenueRejected(code, message)` | order refused (min notional, price band, balance) | record, no retry |
| `VenueNotFound` | unknown order id | reconcile, treat as `REJECTED` after confirmation |
| `VenueSchemaError` | payload did not match expectations | incident SEV2, drop message, keep last good state |

`VenueUnavailable` after a `place_order` MUST be surfaced as `UNKNOWN`, never as
"not submitted" and never re-submitted under a **new** `client_order_id`.

## Order state resolution (FR-068, FR-069)

Adapters report raw venue state; the order state machine in `execution` owns the
interpretation. Four rules bind every adapter:

1. **Partial fills are normal, not an error.** `VenueOrder.filled_quantity` and
   `remaining_quantity` are always populated, and `status=PARTIALLY_FILLED` is returned as
   such. The adapter never waits for a full fill before answering.
2. **`UNKNOWN` is resolved by query, never by resubmission.** `execution` calls
   `get_order(client_order_id=…)` with backoff; three consecutive failures to resolve put
   the venue in `HALTED` and freeze new entries there (FR-069). Adapters must therefore make
   `get_order` by `client_order_id` reliable and cheap — it is the recovery path.
3. **No modify means cancel+replace with the same lineage.** If
   `capabilities.supports_order_modify` is false, `execution` cancels and places a successor
   carrying the same `signed_order_id`; the successor is only placed after the cancel is
   confirmed, so a transient failure can never leave two live entry orders.
4. **Reduce-only never waits on permissions.** A cancel or reduce-only sell is attempted even
   when the venue reports `DEGRADED`; a venue-side rejection of a protective order becomes a
   `SAFETY` incident rather than a silent skip.

## Rate limiting

Each adapter owns a token bucket per endpoint class (`public`, `private`, `order`) sized
from `venue_profile.rate_limit`. Bucket state is per process; a Redis counter enforces the
venue-wide ceiling across services. Default planning values: Indodax public 1 req/s,
private 1 req/s, order 1 req/s, burst ≤ 3. **Re-verify at M0** (research.md R5) and update
`venue_profile`.

## Conformance suite (must pass for any adapter)

`tests/contract/test_venue_port.py`, run against recorded fixtures via `respx` + a fake WS
server:

1. `pairs()` returns a non-empty set with all required numeric fields > 0.
2. `order_book()` is sorted correctly and never returns crossed book without raising.
3. `place_order()` uses `client_order_id` verbatim; replaying the same request produces one
   order (adapter must return the existing order, not create a second).
4. Timeout on `place_order` ⇒ `VenueUnavailable`, and `get_order(client_order_id=…)`
   resolves the true state.
5. `VenueRejected` carries the venue's code and message unmodified.
6. `stream()` reconnects with exponential backoff, emits a `heartbeat_missed` event after
   2× the expected interval, and never yields out-of-order `venue_ts` for one pair.
7. No method accepts or logs a secret; a captured request log contains no API key
   (asserted by a structlog redaction test).
8. `capabilities().max_leverage == 1` and `markets == ["SPOT"]`.
9. `modify_order` on a venue with `supports_order_modify = false` is absent or raises
   `NotImplementedError`, and `execution`'s fallback path is exercised by a dedicated test.
10. A fixture with `status=PARTIALLY_FILLED` is reported unchanged; a timeout followed by a
    `get_order(client_order_id=…)` that returns a fill is the only accepted resolution (no
    second `place_order` in the recorded request log).
