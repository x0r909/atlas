# Contract: Stop Monitor and Protective-Order Path

Covers FR-063 (exits never blocked), FR-067 (separate process, heartbeat, watchdog),
FR-068 (stop sized to the filled quantity), FR-069 (UNKNOWN) and FR-071 (single writer).
This is the interface between the stop-monitor process, `execution`, and the watchdog in
`api-gateway`. It grants no trading authority: every protective order still traverses
`execution`, which is the only component holding venue credentials.

## 1. Why a separate process

A protective stop is the only thing standing between an open position and an unlimited
loss. If it is managed by a thread inside `execution`, then the monitor dies with the
process it is meant to police, and the failure is invisible: nothing is left alive to
notice. Hence:

- `services/stop-monitor` is its own deployable with its own restart policy.
- It holds **no** exchange credentials and cannot place orders directly.
- Its absence is detectable because its heartbeat stops, not because it reports an error.

## 2. Duties (every 10 s)

For each `position` with `status='OPEN'`:

1. Read the venue's open orders (through `execution`'s read path) when
   `venue_profile.native_stop_orders = true`; otherwise treat the locally tracked stop as
   authoritative.
2. If the protective stop is missing, cancelled, or priced above the intended ladder price,
   emit a `ProtectiveStopRequest`.
3. If `position.quantity` changed (partial fill, partial reduction), emit a re-size request so
   the stop quantity always equals the open quantity (FR-068).
4. Record `position.stop_monitor_seen_at` on success.

It never opens, increases, or re-enters a position. It has no path to a `BUY` on a flat
symbol.

## 3. ProtectiveStopRequest

```python
class ProtectiveStopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: UUID
    position_id: UUID
    venue_id: str
    pair: str
    stop_price: Decimal          # must not be further from entry than the current stop
    quantity: Decimal            # equals the position's open quantity
    reason: Literal["MISSING", "CANCELLED", "RESIZE_AFTER_PARTIAL", "TIGHTEN_REQUEST", "HEARTBEAT_RECOVERY"]
    generation: int              # monotonic per position; stale generations are ignored
    requested_at: datetime
```

`execution` accepts it without a `RiskVerdict` (it is reduce-only, FR-063) and applies only
these checks: position is open, `quantity <=` open quantity, `stop_price` does not increase
risk versus the recorded stop, `generation` is not stale. A rejection is returned to the
monitor and raised as an incident — never discarded.

## 4. Heartbeat and watchdog

- The monitor writes `atlas:stopmonitor.heartbeat` every 10 s and refreshes the
  `stop_monitor_heartbeat` row (`last_beat_at`, `positions_checked`, `last_cycle_ms`).
- The watchdog in `api-gateway` (a separate asyncio task from the HTTP server) evaluates the
  freshness of that row every 5 s. If `now - last_beat_at > 30 s`:
  1. write `mode_state = HALTED` (append-only transition, reason `STOP_MONITOR_STALE`);
  2. open a `SAFETY`-class incident `STOP_MONITOR_UNRESPONSIVE`;
  3. publish `atlas:killswitch` with `scope=EXITS_ONLY` so `execution` issues exactly one
     close attempt per open position and then stops;
  4. alert the operator over the remote channel.
- Recovery is operator-driven: restarting the monitor updates the heartbeat, but the system
  stays `HALTED` until the operator lifts it (constitution III, FR-008).
- The monitor also self-checks: if it cannot reach `execution` for 30 s it logs, retries with
  backoff, and reports `DEGRADED` — it does not spin on a dead endpoint.

## 5. Order-state interactions

| Situation | Monitor behaviour |
| --- | --- |
| Entry order `PARTIALLY_FILLED` | Stop sized to `filled_quantity`; remainder cancelled by `execution` after 45 s (FR-068) |
| Entry order `UNKNOWN` | No stop can be sized yet; venue freezes for new entries (FR-069); monitor keeps watching, and if the order resolves as filled it places the stop on the next cycle |
| Close order `UNKNOWN` | Position stays `CLOSING`; monitor keeps the protective stop alive, because a failed close is not a closed position |
| Close rejected twice | `execution` alerts the operator and retries with a market order inside the slippage guard; the monitor does not duplicate that attempt (FR-070) |
| HALTED / breaker / spend cap | Protectives proceed normally; entries are already blocked upstream (FR-063, SC-021) |

## 6. Conformance tests (M3)

1. Kill the monitor process ⇒ HALTED within 5 s, one close attempt per position recorded,
   alert emitted, no new entries.
2. Delete the venue-side stop ⇒ re-placed within one cycle (≤ 10 s) with the same quantity.
3. Partial fill of 40% ⇒ stop quantity equals the filled quantity within one cycle, and the
   remainder is cancelled after the timeout.
4. `ProtectiveStopRequest` with a wider stop than the recorded one ⇒ rejected and recorded.
5. Stale `generation` ⇒ ignored without touching the venue.
6. Monitor running with no open positions ⇒ heartbeat continues, zero orders emitted.
