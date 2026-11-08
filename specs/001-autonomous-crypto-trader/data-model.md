# Phase 1 Data Model — ATLAS

Derived from `spec.md` (FR-001…FR-061) and `research.md`. This is the logical model; the
physical schema lives in `infra/db/migrations` (Alembic) and must be generated from the
pydantic models in `packages/schemas` so that the wire contracts and the database cannot
drift apart.

Conventions:
- All times are `timestamptz` in UTC; `venue_ts` (exchange-reported) is stored separately
  from `ingested_at` so latency is measurable.
- Money and quantities use `numeric(28,10)`; never floats, never `float8`. Prices are
  `numeric(20,8)`. A single `packages/common/money.py` module owns rounding and tick/step
  conversion.
- Every table that a decision depends on carries a monotonic `seq` used to build the input
  snapshot hash.
- Soft deletes are not used for audit-bearing tables; they are append-only.

---

## 1. Rollout and configuration

### `operator`
| Field | Type | Notes |
| --- | --- | --- |
| id | uuid PK | single row in practice |
| username | text unique | |
| totp_secret_enc | bytea | sops-encrypted, never logged |
| passkey_credentials | jsonb | optional WebAuthn credentials |
| telegram_user_id | bigint unique | kill-switch allowlist |
| created_at | timestamptz | |

### `mode_state`
Append-only history of the rollout stage; the current mode is the latest row.

| Field | Type | Notes |
| --- | --- | --- |
| id | uuid PK | |
| mode | enum `BACKTEST, PAPER, CO_PILOT, AUTONOMOUS, HALTED` | `CO_PILOT` = constitution LIVE (small capital) |
| previous_mode | same enum, nullable | |
| reason_code | enum `OPERATOR_PROMOTION, OPERATOR_DEMOTION, AUTO_DEMOTE, STARTUP_DEFAULT, KILL_SWITCH, RECOVERY` | |
| capital_cap_idr | numeric | `30000` in CO_PILOT; `null` = full capital |
| actor | enum `OPERATOR, SYSTEM` | SYSTEM only for AUTO_DEMOTE / STARTUP_DEFAULT |
| evidence_ref | uuid → `promotion_evaluation` | required when reason = OPERATOR_PROMOTION |
| created_at | timestamptz | |

Invariants: exactly one current row; transition graph is
`PAPER→CO_PILOT→AUTONOMOUS`, `*→HALTED`, `HALTED→{PAPER,CO_PILOT}`, `CO_PILOT→PAPER`,
`AUTONOMOUS→CO_PILOT|PAPER`. `STARTUP_DEFAULT` always resolves to `PAPER` (FR-001,
constitution IV). Enforced in code **and** by a trigger rejecting illegal pairs.

### `risk_config`
Versioned global limits; the active row is referenced by `config_hash` in every verdict.

| Field | Type | Default |
| --- | --- | --- |
| version | int PK | |
| risk_per_trade_pct | numeric(6,3) | 0.5 |
| max_position_pct | numeric(6,3) | 25.0 |
| max_asset_exposure_pct | numeric(6,3) | 50.0 |
| max_total_exposure_pct | numeric(6,3) | 100.0 |
| max_daily_loss_pct | numeric(6,3) | 2.0 |
| max_drawdown_pct | numeric(6,3) | 10.0 |
| min_rr_net | numeric(6,3) | 1.5 |
| max_slippage_pct | numeric(6,3) | 0.3 |
| max_spread_multiple | numeric(6,2) | 3.0 |
| volatility_breaker_sigma | numeric(6,2) | 5.0 |
| stale_price_seconds | int | 10 |
| cooldown_after_losses | int / hours | 3 / 4 |
| time_stop_hours | int | 120 |
| max_open_positions | int | 5 |
| max_orders_per_hour | int | 6 |
| llm_daily_cap_idr | int | 5000 |
| llm_monthly_cap_idr | int | 250000 |
| effective_from | timestamptz | |

A new row is written by the operator only; the previous row is never mutated. Verdicts
store `risk_config_version` so old decisions stay reproducible (Principle VII).

### `llm_spend`
| Field | Type | Notes |
| --- | --- | --- |
| id | uuid PK | |
| decision_cycle_id | uuid | |
| tier | enum `CHEAP, STRONG` | |
| provider / model_id / model_version | text | |
| input_tokens / output_tokens | int | |
| cost_idr | numeric(12,2) | computed from `llm_price` at call time |
| latency_ms | int | |
| outcome | enum `OK, TIMEOUT, INVALID_OUTPUT, RATE_LIMITED, BUDGET_BLOCKED` | |
| created_at | timestamptz | |

Indexes: `(created_at)`, `(decision_cycle_id)`. Daily/monthly sums are materialized views
refreshed every 5 min; the pre-call guard reads the view plus in-flight reservations.

---

## 2. Venues and market data

### `venue`
| Field | Type | Notes |
| --- | --- | --- |
| id | text PK | `indodax` |
| display_name | text | |
| regulator | enum `OJK, BAPPEBTI_LEGACY, NONE` | live trading requires `OJK` (FR-054) |
| enabled_for_live | boolean | config toggle, audited |
| quote_currencies | text[] | `{IDR}` |
| api_base / ws_url | text | |
| status | enum `ACTIVE, DEGRADED, DISABLED` | |

### `venue_profile` (versioned; one active row per venue)
| Field | Type |
| --- | --- |
| venue_id | text FK |
| version | int |
| fee_maker_pct / fee_taker_pct | numeric(7,5) |
| cfx_fee_pct | numeric(7,5) |
| tax_pph_pct / tax_ppn_pct | numeric(7,5) |
| tax_applies_to | enum `SELL_ONLY, BOTH_SIDES` |
| min_notional_idr | numeric(18,2) — 10000 for Indodax |
| min_order_step / tick_size | jsonb per pair |
| rate_limit | jsonb `{public_rps, private_rps, burst}` |
| max_leverage | int — always 1 |
| native_stop_orders | boolean — whether the venue accepts a resting stop/bracket order; `false` for Indodax until M0 proves otherwise, which selects the client-side stop monitor (FR-067) |
| cost_model_version | text |
| verified_at | timestamptz |

Every `RiskVerdict` and `BacktestRun` references `venue_profile_id`; a profile change
invalidates promotion evidence, never silently re-labels it.

### `equity_baseline` (one active row per stage)
The two reference values every loss rule reads. Written by the scheduler, never by an agent.
| Field | Notes |
| --- | --- |
| id uuid PK | |
| mode_stage | enum `CO_PILOT, AUTONOMOUS` — PAPER and BACKTEST keep their own simulated rows |
| day_start_equity_idr | global mark-to-market equity at 00:00 WIB of `day_started_on` (FR-066) |
| day_started_on | date in Asia/Jakarta; a new calendar day opens a new row and the previous day's is closed |
| peak_equity_idr | global mark-to-market high-water mark; seeded at stage entry, raised whenever equity exceeds it, and **reset only when a new Live stage begins** |
| stage_started_at | start of the Live stage this peak belongs to |
| created_at, closed_at | |

Daily loss = `(day_start_equity_idr − equity_now) / day_start_equity_idr`; drawdown =
`(peak_equity_idr − equity_now) / peak_equity_idr`. Both are computed across all venues,
including unrealised PnL, and both reset per the rules above — never on a rolling 24 h window.

### `market_bar` (hypertable, 1-minute)
`(venue_id, pair, timeframe, ts)` PK; `open, high, low, close, volume numeric`, `trade_count`,
`source enum LIVE|BACKFILL|SYNTHETIC`.
Continuous aggregates: `market_bar_5m`, `market_bar_1h`. Retention 90 days raw, 2 years
aggregates; compression after 7 days (Timescale policy).

### `quote_snapshot`
Latest known book/top-of-book per pair: `pair PK`, `bid, ask, bid_size, ask_size`, `ts`,
`venue_ts`, `seq`. Staleness is computed at read time, never stored as a boolean.

### `order_book_snapshot`
Append-only, referenced by decisions: `id uuid PK`, `pair`, `ts`, `bids jsonb`, `asks jsonb`,
`checksum`. Retention 30 days (volume ≈ 96/day/decision, ~50 further ad-hoc).

### `data_health`
Per source: `source_id PK`, `last_message_at`, `expected_interval_s`, `status
enum OK|STALE|DOWN`, `consecutive_failures`, `updated_at`.

---

## 3. News and features

### `news_item`
| Field | Notes |
| --- | --- |
| id uuid PK | |
| source_id / source_name | RSS feed id + display name |
| url_canonical unique | dedupe key |
| title, summary, body_text | text, untrusted |
| language | `id`/`en` |
| published_at, fetched_at | |
| tickers text[] | extracted |
| injection_flags jsonb | matched patterns |
| excluded_from_prompts bool | true when flagged |
| simhash bigint | near-duplicate detection |
| cluster_id uuid | groups the same story across outlets |

### `news_embedding`
`news_item_id PK FK`, `embedding vector(384)`, `model_id`. HNSW index with
`m=16, ef_construction=64`. Created at M6, extension installed at M0.

### `feature_snapshot`
Per cycle, per pair: `(cycle_id, pair)` PK; `jsonb` of deterministic indicators (EMA, ATR,
RSI, realized vol, spread median, depth at 0.1/0.3/0.5%, 24 h range position) plus
`config_hash`. Fully recomputable from bars — never the sole source of a decision input.

---

## 4. Decisions and agents

### `decision_cycle`
| Field | Notes |
| --- | --- |
| id uuid PK | |
| mode | enum (mode at cycle start) |
| started_at, finished_at | |
| trigger | `SCHEDULE, OPERATOR, EVENT` |
| leader_lock_id | text |
| outcome | `COMPLETED, SKIPPED, HALTED, ERROR, BUDGET_BLOCKED` |
| skip_reason | text |

### `agent_run`
One row per agent step: `id`, `cycle_id`, `agent enum ANALYST|STRATEGIST|CRITIC`,
`state_from`, `state_to`, `prompt_hash`, `prompt_text` (encrypted-at-rest column is out of
scope for v1; access is restricted and the column is excluded from logs), `model_id`,
`model_version`, `params jsonb`, `raw_output`, `parsed_output jsonb`, `tokens`, `cost_idr`,
`latency_ms`, `error_code`.

### `trade_proposal`
| Field | Notes |
| --- | --- |
| id uuid PK | |
| cycle_id, agent_run_id | |
| pair, side | `BUY` (spot, long-only) |
| entry_type | `LIMIT, MARKET_LIMIT` |
| entry_price, stop_price, target_price | numeric; stop is mandatory (FR-019) |
| stop_distance_pct | generated |
| intended_notional_idr | agent's suggestion |
| rationale_text | untrusted model text, stored, never re-fed as instruction |
| confidence | numeric(3,2) |
| status | enum `DRAFT, VALIDATED, REJECTED_INVALID, SUBMITTED, EXPIRED` |
| schema_version | text |

Validation rules (pydantic): `stop_price < entry_price < target_price` for BUY;
`horizon_hours ∈ [4, 120]`; `intended_notional_idr ≥ venue min notional`; all prices a
multiple of tick size; unknown fields rejected (`model_config extra="forbid"`).

### `management_action`
`id`, `position_id`, `type enum MOVE_STOP, CLOSE, TIME_STOP`, `new_stop`,
`reason`, `agent_run_id nullable` (time-stop and stop management may be deterministic),
`status`. Scale in, scale out, and partial take profit are not representable in v1 (FR-020).

### `risk_verdict` (append-only)
| Field | Notes |
| --- | --- |
| id uuid PK | |
| proposal_id / management_action_id | |
| approved bool | |
| rule_results jsonb | array of `{rule_id, passed, observed, limit, message}` for **every** rule, not just the failing one |
| primary_rule_id | the blocking rule, null when approved |
| sized_quantity, sized_notional_idr, realised_risk_idr, realised_risk_pct | |
| rr_net | numeric(6,3) |
| cost_breakdown jsonb | fee, cfx, tax, slippage allowance |
| risk_config_version, venue_profile_id, portfolio_snapshot_id | |
| input_hash | sha256 of the canonical snapshot |
| signature | HMAC-SHA256 over the canonical verdict payload (approved verdicts only, null for rejects) |
| expires_at | `verdict_at + 60 s` — after this the verdict can never produce an order (FR-062) |
| consumed_at / consumed_by_order_id | set exactly once, when `execution` accepts the signed order; a second attempt is refused and audited |
| verdict_at | timestamptz |

Rule ids (stable strings, used by the UI and by tests): `VENUE_NOT_LIVE_ENABLED`,
`MARKET_NOT_SPOT`, `STALE_PRICE`, `STALE_NEWS_IRRELEVANT`, `SPREAD_BREAKER`,
`VOLATILITY_BREAKER`, `NO_STOP_LOSS`, `STOP_TOO_WIDE`, `MIN_ORDER_EXCEEDS_RISK_BUDGET`,
`POSITION_SIZE_EXCEEDS_MAX`, `ASSET_EXPOSURE_EXCEEDS_MAX`, `TOTAL_EXPOSURE_EXCEEDS_MAX`,
`DAILY_LOSS_LIMIT`, `MAX_DRAWDOWN`, `RR_BELOW_MIN`, `MAX_OPEN_POSITIONS`,
`ORDER_RATE_LIMIT`, `COOLDOWN_ACTIVE`, `MACRO_BLACKOUT`, `DUPLICATE_PROPOSAL`,
`RECONCILIATION_MISMATCH`, `BUDGET_BLOCKED`.

### `signed_order`
The only object `execution` accepts: `id`, `verdict_id` (unique — one order per verdict),
`proposal_id`, `venue_id`, `pair`, `side`, `type`, `quantity`, `limit_price`,
`client_order_id` (deterministic: `atlas-{cycle_short}-{proposal_short}-{attempt}`),
`stop_price`, `signature` (HMAC-SHA256 over canonical payload + verdict id),
`signed_at`, `expires_at` (≤ 60 s TTL).

The signature binds `proposal_id, venue_id, symbol, side, quantity` — altering any field
invalidates it. `verdict_id` is unique, so replaying the same signed order is impossible;
the `risk_verdict.consumed_at` write and the order insert happen in one transaction so a
crash between them cannot leak a second order.

---

## 5. Orders, fills, positions

### `order`
| Field | Notes |
| --- | --- |
| id uuid PK | internal |
| venue_id, venue_order_id | `venue_order_id` nullable until ack; unique per venue when present |
| client_order_id unique per venue | idempotency key (FR-031) |
| signed_order_id | |
| pair, side, type, quantity, limit_price | |
| status | enum `PENDING_NEW, OPEN, PARTIALLY_FILLED, FILLED, CANCELED, REJECTED, EXPIRED, UNKNOWN` |
| filled_quantity, avg_fill_price | `filled_quantity < quantity` while `PARTIALLY_FILLED` |
| remaining_quantity, remainder_action | `CANCEL_PENDING / CANCELED / NONE` — a partial fill's remainder is cancelled after `partial_fill_timeout_s` (45 s) unless it completes first (FR-068) |
| unknown_since, resolution_attempts, resolved_at | non-null only in `UNKNOWN`; drives the query-not-resubmit loop and the 3-attempt halt (FR-069) |
| fees_paid_idr, tax_paid_idr | |
| reject_reason | |
| created_at, submitted_at, updated_at | |
| raw_responses jsonb | truncated venue payloads for audit |

State machine (every transition written to the audit record):

```text
PENDING_NEW ──ack──> OPEN ──fill──> FILLED
     │                 ├──partial──> PARTIALLY_FILLED ──fill──> FILLED
     │                 │                    └──remainder canceled──> CANCELED (filled qty stays)
     │                 ├──cancel──> CANCELED
     │                 └──expire──> EXPIRED
     ├──reject──> REJECTED
     └──timeout/no confirmation──> UNKNOWN ──resolved by query──> OPEN | FILLED | CANCELED | REJECTED
                                        └──3 failed resolutions──> HALTED (venue frozen, FR-069)
```

`UNKNOWN` is non-terminal and is the only state that freezes a venue: no new entry orders
on that venue, exits and protective orders still allowed (FR-063), and the freeze is lifted
only by a confirmed query result. A partial fill never leaves the position unprotected: the
stop order is sized to `filled_quantity` and re-sized on every subsequent fill or reduction.

### `fill`
`id`, `order_id`, `venue_trade_id unique`, `quantity`, `price`, `fee_idr`, `tax_idr`,
`filled_at`, `is_maker`.

### `position`
| Field | Notes |
| --- | --- |
| id uuid PK, venue_id, pair | unique partial index: one open position per (venue, pair) |
| quantity, avg_entry_price, notional_idr | |
| stop_price, stop_order_id | stop must exist within 30 s of fill (FR-019); `stop_order_id` null when the venue has no native stop and the monitor owns protection |
| stop_protection | enum `NATIVE_ORDER, MONITORED` — `MONITORED` means the stop-monitor process is responsible |
| stop_monitor_seen_at | last heartbeat that confirmed this position's stop is in place (FR-067) |
| stop_confirmation | enum `CONFIRMED, UNCONFIRMED`, default `CONFIRMED`; `UNCONFIRMED` means the venue rejected or never acknowledged the protective stop after the fill (FR-033) |
| stop_unconfirmed_at | NOT NULL when `stop_confirmation='UNCONFIRMED'`, NULL otherwise (check constraint pairs the two columns) |
| target_price | |
| opened_at, time_stop_at | `opened_at + 120h` |
| status | enum `OPEN, CLOSING, CLOSED, STOP_LOST` |
| realised_pnl_idr, fees_idr, taxes_idr | |
| closed_at, close_reason | `TARGET, STOP, TIME_STOP, OPERATOR, KILL_SWITCH, MODE_FALLBACK` |

### `stop_monitor_heartbeat`
Single-row-per-process liveness record for the stop-monitor: `process_id PK`, `started_at`,
`last_beat_at`, `beat_interval_s` (10), `positions_checked int`, `last_cycle_ms`.
A reader that sees `now − last_beat_at > 30 s` writes `mode_state = HALTED`, opens a
`SAFETY`-class incident, and issues exactly one close attempt per open position (FR-067,
SC-022). The row is not used to grant authority — only its absence is load-bearing.

### `portfolio_snapshot`
`id`, `ts`, `equity_idr`, `cash_idr`, `positions_value_idr`, `unrealised_pnl_idr`,
`realised_pnl_day_idr`, `drawdown_pct`, `peak_equity_idr`, `per_venue jsonb`,
`reconciled bool`. One row per cycle plus one per reconcile; the Risk Engine consumes the
latest *reconciled* row.

---

## 6. Audit, incidents, promotion

### `audit_record` (append-only, hash-chained)
| Field | Notes |
| --- | --- |
| seq bigint PK | gapless per chain |
| record_type | `DECISION, ORDER, MODE_CHANGE, CONFIG_CHANGE, KILL_SWITCH, PROMOTION, INCIDENT, RECONCILE` |
| payload jsonb | canonical JSON |
| prev_hash, hash | `hash = sha256(prev_hash ‖ canonical_payload ‖ seq)` |
| created_at | |

A nightly verifier walks the chain and raises an incident on any break (constitution VII; audit record per FR-037). No
`UPDATE`/`DELETE` grants on this table for application roles.

### `decision_record`
The replay bundle (Principle VII): `id`, `cycle_id`, `proposal_id`, `agent_runs uuid[]`,
`market_snapshot_hash`, `news_snapshot_hash`, `portfolio_snapshot_id`,
`order_book_snapshot_id`, `prompt_bundle jsonb` (exact rendered prompts),
`model_meta jsonb`, `raw_outputs jsonb`, `verdict_id`, `orders uuid[]`,
`exchange_responses jsonb`, `code_version`, `config_hash`, `created_at`.
Retention: 2 years (≈ 270 MB/90 days at 30 KB — comfortable).

### `incident`
| Field | Notes |
| --- | --- |
| id, opened_at, closed_at | |
| class | `SAFETY` / `OPERATIONAL` |
| kind | safety: `ORDER_WITHOUT_STOP, HARD_LIMIT_BREACH, DUPLICATE_ORDER, POSITION_MISMATCH, KILL_SWITCH_FAILURE, STOP_UNCONFIRMED, ADOPTED_POSITION_UNPROTECTED, STOP_MONITOR_UNRESPONSIVE`; operational: `VENUE_DOWN, STALE_DATA, LLM_FAILURE, BUDGET_THROTTLE, RECONCILE_DELAY, LOG_CHAIN_BREAK` |
| severity | `SEV1` (safety) / `SEV2` / `SEV3` |
| auto_action | `NONE, HALTED, THROTTLED` |
| root_cause, remediation, operator_ack_at | |

Open `SAFETY` incidents block promotion (FR-059); `OPERATIONAL` ones are reported.

### `promotion_evaluation`
| Field | Notes |
| --- | --- |
| id uuid PK | |
| from_stage, to_stage | PAPER→CO_PILOT or CO_PILOT→AUTONOMOUS |
| window_start, window_end | |
| metrics jsonb | days, cycles, closed_trades, max_dd_pct, incidents_open, chaos_drills, kill_switch_drills, slippage_median/p95/max, reconcile_mismatches, llm_cap_breaches |
| criteria jsonb | one entry per FR-059 criterion: `{criterion, required, observed, passed}` |
| passed bool, failing_criteria text[] | |
| operator_confirmed_by, operator_confirmed_at | null until confirmed |
| resulting_mode_state_id | set only after confirmation |

### `backtest_run`
`id`, `name`, `window_start/end`, `pairs text[]`, `strategy_config_hash`,
`venue_profile_id`, `cost_model_version`, `code_version`, `status
enum QUEUED|RUNNING|DONE|FAILED`, `metrics jsonb`, `equity_curve jsonb` (downsampled),
`trade_count`, `created_at`. Trades are stored in `backtest_trade` with the same columns as
`order`/`fill` so the UI can render them with the same component.

---

## Entity relationships (text form)

`operator` 1—N `audit_record`; `mode_state` N—1 `promotion_evaluation`;
`decision_cycle` 1—N `agent_run` 1—N `trade_proposal` 1—1 `risk_verdict` 1—1 `signed_order`
1—1 `order` 1—N `fill`; `order` N—1 `position` (aggregated); `position` N—1 `stop_monitor_heartbeat`
(the beat that last confirmed its protection); `equity_baseline` 1—1 current `mode_stage`;
`venue` 1—N `venue_profile` (versioned) 1—N `order`/`position`/`market_bar`; `news_item` 1—1 `news_embedding`;
`decision_record` references one cycle, its agent runs, verdict, orders, and the three
snapshot hashes; `incident` may reference a `decision_record` or a `reconcile_run`.

## Cross-cutting invariants (enforced in code + DB constraints)

1. A `signed_order` requires an `approved` verdict whose `risk_config_version` and
   `venue_profile_id` are current at signing time; otherwise signing fails.
2. A verdict is usable once: `consumed_at` is set at most once, `expires_at > now` at
   acceptance, and the signature must verify over the exact payload. Consumption and the
   `order` insert share one transaction (FR-062).
3. A `position` in `OPEN` always has a non-null `stop_price` and either a live
   `stop_order_id` or `stop_protection='MONITORED'` with a heartbeat newer than 30 s;
   violating this raises a `SAFETY/ORDER_WITHOUT_STOP` incident.
4. Sum of `position.notional_idr` where `status='OPEN'` per asset ≤ 50% equity and in total
   ≤ 100% equity across all venues (FR-024, FR-055).
5. `order.client_order_id` is unique per venue; a retry with the same id returns the
   existing order rather than submitting again (FR-031).
6. Every order row, position row, balance row, and verdict carries `venue_id`.
7. No `trade_proposal` may be created in `BACKTEST` mode by a live agent run: backtest
   agent runs are synthetic and marked `simulated=true`.
8. `equity_baseline.day_started_on` is the current Asia/Jakarta date and `peak_equity_idr`
   belongs to the current `mode_stage`; a stale baseline is refused by the Risk Engine
   rather than silently recomputed (FR-066).
9. Exactly one `UNKNOWN` order may exist per venue at a time; while it exists no new entry
   order is accepted for that venue (FR-069).
10. Reduce-only mutations (`CLOSE`, protective-stop place/modify, entry cancel) are never
    blocked by mode, breaker, spend cap, or `UNKNOWN` freeze — only by signature/lifetime
    validity, so an exit can always reach the venue (FR-063, SC-021).
11. A `position` with `status='OPEN'` and `stop_confirmation='UNCONFIRMED'` must be closed or
    confirmed within one execution cycle (FR-033). Surviving a cycle still `UNCONFIRMED` is a
    safety breach: it raises `SAFETY/STOP_UNCONFIRMED` (`SEV1`), blocks new entries, and
    alerts the operator; `stop_unconfirmed_at` records when the state began.
