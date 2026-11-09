# Incident definitions

An incident is a row in `incident`, opened by the component that detected the condition and
closed by a human. This page fixes what each `kind` means, who detects it, and what the system
does by itself. The vocabulary is the database enum: adding a kind is a migration, not a
free-text convention.

## Safety incidents (FR-061)

These five are what "safety incident" means anywhere else in the documentation. All are
`SEV1` and all halt.

| Kind | Means | Detected by | Automatic action |
|---|---|---|---|
| `ORDER_WITHOUT_STOP` | A position is open and no confirmed protective stop exists at the venue for longer than a cycle | stop monitor, reconcile | `HALTED`, close reduce-only, alert |
| `HARD_LIMIT_BREACH` | An order or position exceeds a hard limit that no configuration may widen (per-trade risk, exposure, drawdown) | risk engine (pre-trade), reconcile (post-hoc) | `HALTED`, no new entries |
| `DUPLICATE_ORDER` | Two orders exist for the same proposal/verdict/lineage, or the venue reports an order we did not place | execution, reconcile | `HALTED`, cancel the duplicate |
| `POSITION_MISMATCH` | Venue and database disagree on an open position after a reconcile, or an adopted position is not protected by the end of the monitor's first cycle (`ADOPTED_POSITION_UNPROTECTED`) | reconcile | `HALTED` when unprotected, entries blocked when unresolved |
| `KILL_SWITCH_FAILURE` | A halt was requested and did not take effect — the kill switch publish failed, or the mode did not change | api-gateway, watchdog | alert immediately; the operator must halt at the venue |

`STOP_UNCONFIRMED` and `STOP_MONITOR_UNRESPONSIVE` are the specific forms the stop path
reports: the first when the venue rejected or never acknowledged a protective stop, the second
when the monitor's heartbeat is older than its threshold. Both are `SEV1` and both halt.

## Operational incidents

`SEV2` unless noted; they degrade the system without hiding a loss of protection.

| Kind | Means | Automatic action |
|---|---|---|
| `VENUE_DOWN` | Consecutive transport failures past the threshold | entries blocked on that venue, exits queue |
| `STALE_DATA` | `data_health` row older than twice its expected interval, or a quote older than 10 s | breaker active, entries blocked |
| `LLM_FAILURE` | Model call timed out, returned unparseable output, or was rate-limited three times in a cycle | cycle recorded as `SKIPPED` with reason |
| `BUDGET_THROTTLE` | Daily LLM spend crossed the degradation threshold | `reduce_frequency`, then `THROTTLED_ENTRIES` |
| `RECONCILE_DELAY` | No full reconcile in twice the 5-minute interval | entries blocked |
| `LOG_CHAIN_BREAK` | `verify()` found a hash mismatch or a gap | `SEV1`, `HALTED`, nothing writes until a human has seen it |
| `POISON_EVENT` | An event failed delivery three times and went to `atlas:deadletter` | `SEV3` if the stream is not on the order path, `SEV2` otherwise |

## Severity

- **SEV1** — money or protection is at risk right now. Halt, alert, one human on it.
- **SEV2** — the system is degraded and one capability is missing. Entries blocked, exits fine.
- **SEV3** — logged for review; no automatic action.

## What every incident must carry

`kind`, `severity`, `auto_action`, `opened_at`, and enough in `detail` to answer "which venue,
which pair, which position, which order, what value, what limit". JSON keys mirror the
`RuleResult` fields so an incident and the verdict that refused the trade read the same way.

## Closing

An incident is closed with `root_cause` and `remediation` filled in. A closed incident without
a cause is a lie that outlives the outage: it is what the next promotion evaluation will count
as "no incidents".
