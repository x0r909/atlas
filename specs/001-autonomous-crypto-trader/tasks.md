---
description: "Task list for ATLAS milestones M0–M3"
---

# Tasks: ATLAS — M0–M3 (Foundation → Market Data → News → Risk & Execution in Paper)

**Input**: Design documents from `/specs/001-autonomous-crypto-trader/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md), `.specify/memory/constitution.md` v2.0.0

**Scope**: **M0–M3 only.** M4 (agents v1), M5 (backtest lab), M6 (derivatives/on-chain/debate), M7 (live co-pilot), M8 (autonomous) are deliberately **not** broken down here. Anything they need is either stubbed or deferred, and no task below may quietly start them.

**Tests**: Included and mandatory — constitution IX (Test-First & Chaos) requires every task to start from a failing test, and the operator explicitly asked for the risk/order-path test suite listed in "Mandatory Test Coverage". Tests come before implementation inside each phase.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1 risk-gated paper trading, US2 kill switch/fail-safe, US3 news pipeline, US4 terminal, US5 traceability/replay
- Every task names its file path

## Path Conventions

Monorepo per [plan.md](./plan.md) § Project Structure: `services/<name>/`, `apps/terminal-web/`, `packages/{schemas,common}/`, `infra/`, `tests/`.

## Milestone → Phase Map

| Milestone | Phases | Exit gate |
| --- | --- | --- |
| **M0 Foundation** | 1, 2 (part), 3 | CI green, compose up, PAPER default, Indodax facts verified |
| **M1 Market data + terminal skeleton** | 2 (part), 4 | Fresh bars/quote in DB, stale detection, terminal shows live price |
| **M2 News pipeline** | 5 | ≥3 RSS sources ingested, deduped, screened, stored |
| **M3 Risk + execution (paper)** | 6, 7, 8, 9 | quickstart S1–S11, S13–S18 green; 72 h paper run |

---

## Phase 1: Setup (M0 — repository, tooling, infrastructure)

**Purpose**: A buildable, lintable, deployable skeleton before any domain code.

- [X] T001 Create the monorepo tree from plan.md (`services/`, `apps/terminal-web/`, `packages/{schemas,common}/`, `infra/{compose,db,observability,secrets,vps}/`, `docs/{adr,runbooks,operator}/`, `tests/{contract,integration,chaos,unit,fixtures}/`)
- [X] T002 [P] Create root `pyproject.toml` with ruff, mypy strict for `packages/schemas`, `services/risk-engine`, `services/execution`, pytest + pytest-asyncio + respx + time-machine + Hypothesis
- [X] T003 [P] Initialize `packages/schemas/pyproject.toml` and `packages/common/pyproject.toml` as installable workspace packages with pydantic v2
- [X] T004 [P] Scaffold `apps/terminal-web/` (Next.js 15 App Router, TypeScript strict, Tailwind 4, no state/data library per plan.md) (built on Tailwind 4.3.5)
- [X] T005 [P] Add `.github/workflows/ci.yml`: ruff, mypy, pytest, `pip-audit`, `gitleaks`, and a `markdownlint` job over `specs/**` (docs job lints the repo's own prose; `specs/**` is owned by the Spec Kit templates)
- [X] T006 [P] Write `infra/compose/docker-compose.yml` with the nine services, `postgres:16` + TimescaleDB + pgvector, Redis 7 (`maxmemory 384mb`, `maxmemory-policy noeviction`), and `ATLAS_MODE=PAPER` as the shipped default (built on `timescale/timescaledb-ha:pg16`, which bundles pgvector)
- [X] T007 [P] Initialize Alembic in `infra/db/` with async env and a `versions/` skeleton
- [X] T008 [P] Define the sops+age layout in `infra/secrets/` (encrypted `paper.env`, `live.env.template`), document `/etc/atlas/age.key` (0600) and the tmpfs decrypt target `/run/atlas/*.env` (as built: layout lives in `infra/age/` with templates inline in its README and `scripts/render-credentials.sh`)
- [X] T009 [P] Write `infra/vps/bootstrap.sh` (Ubuntu 24.04, chrony mandatory, Tailscale only ingress, static IPv4 requirement, systemd units, nightly `pg_dump` off-box) (plus `infra/vps/backup.sh` and `atlas-backup.timer` for the nightly off-box dump)
- [X] T010 [P] Implement `packages/common/atlas_common/logging.py`: structlog JSON to journald with a redaction filter for API keys, HMAC key, TOTP seed, and env dumps, plus `tests/unit/test_logging_redaction.py` asserting a secret never reaches the log stream (FR-040)
- [X] T011 [P] Implement `packages/common/atlas_common/money.py` (IDR `Decimal` rounding, `floor_to_step`, `tick_round`, fee/tax helpers) with `tests/unit/test_money.py`, including a `floor_to_step` property test that never rounds up
- [X] T012 [P] Implement `packages/common/atlas_common/clock.py` (injected clock, Asia/Jakarta day boundary, monotonic heartbeat timestamps) with `tests/unit/test_clock.py` covering the 00:00 WIB rollover
- [X] T012a [P] Write `tests/unit/test_mode_default.py` failing first (FR-002, SC-012): unset `ATLAS_MODE` ⇒ `PAPER`; `ATLAS_MODE=PAPR` ⇒ the service refuses to start with `MODE_UNKNOWN` and never falls back to a Live mode; a missing or unreadable `mode_state` row ⇒ `PAPER`; and no configuration path maps an invalid value to Co-pilot or Autonomous
- [X] T012b Implement `packages/common/atlas_common/config.py` mode resolution to pass T012a (unset/invalid/missing ⇒ `PAPER`, unknown env value ⇒ refuse to start) and wire it into the service bootstrap used by every service

---

## Phase 2: Foundational (M0–M1 — blocking prerequisites)

**⚠️ CRITICAL**: No M2/M3 work starts until this phase is complete. T023 additionally **blocks M3** (see Dependencies).

**Purpose**: Shared schema, database, event bus, mode state, audit chain, auth, and the venue port that everything else stands on.

### Schemas (contracts → code)

- [x] T013 [P] Write `packages/schemas/atlas_schemas/venue.py` (`VenueCapabilities`, `VenueProfile`, `PairInfo`, `Ticker`, `OrderBook`, `Balance`, `VenueOrder`, `VenueOrderAck`, `PlaceOrderRequest`, `VenueTrade`) exactly as `contracts/venue-adapter.md` defines them, with `extra="forbid"`
- [x] T014 [P] Write `packages/schemas/atlas_schemas/order.py` (`SignedOrder` with signature/`expires_at`, `ProtectiveStopRequest` with `generation`, order-status enum `PENDING_NEW, OPEN, PARTIALLY_FILLED, FILLED, CANCELED, REJECTED, EXPIRED, UNKNOWN`, `VerdictRefusal` codes)
- [x] T015 [P] Write `packages/schemas/atlas_schemas/risk.py` (`TradeProposal`, `ManagementAction` with `type: Literal["MOVE_STOP","CLOSE","TIME_STOP"]`, `RuleResult`, `RiskVerdict` with `signature`, `expires_at`, `consumed_at`, `CostBreakdown`) and a validator that rejects `target <= entry`, `stop >= entry`, and horizon outside 4–120 h (FR-016)
- [x] T016 [P] Write `packages/schemas/atlas_schemas/events.py` (event envelope `{event_id, event_type, schema_version, produced_at, producer, correlation_id, causation_id, venue_id, payload}`) with round-trip tests
- [x] T017 Generate TypeScript types from the pydantic models into `apps/terminal-web/src/lib/schema.d.ts` and fail CI when they drift (a `make types` check) — **requires T013–T016**, so it is deliberately not `[P]`: there are no models to generate from until they exist

### Database

- [x] T018 Create `infra/db/versions/0001_core.py`: `operator`, `mode_state`, `risk_config` (defaults 0.5%/25%/50%/100%/2%/10%/1.5 R:R/Rp5.000/Rp250.000), `venue`, `venue_profile` (including `native_stop_orders`, `supports_order_modify`), `equity_baseline`; `venue_profile` also carries `cfx_fee_pct_assumed` and `tax_buy_pct_assumed` (`NOT NULL`, used only by Paper/Backtest)
- [x] T019 Create `infra/db/versions/0002_market.py`: `market_bar` TimescaleDB hypertable + `market_bar_5m`/`market_bar_1h` continuous aggregates, `quote_snapshot`, `order_book_snapshot`, `data_health`, compression after 7 days, 90-day retention
- [x] T020 Create `infra/db/versions/0003_decision.py`: `decision_cycle`, `agent_run`, `trade_proposal`, `management_action`, `risk_verdict` (unique `consumed_at` write path), `signed_order` (unique `verdict_id`), `order`, `fill`, `position` (partial unique index one open per venue+pair; `stop_confirmation` enum default `CONFIRMED`, `stop_unconfirmed_at` with the paired check constraint, invariant 11), `portfolio_snapshot`, `stop_monitor_heartbeat`; `risk_verdict` also carries `cost_model_source` (`VERIFIED|ASSUMED`) so an assumed-cost verdict can never size a Live order
- [x] T021 Create `infra/db/versions/0004_audit.py`: `audit_record` (gapless `seq`, `prev_hash`/`hash`, no UPDATE/DELETE grants for the app role), `decision_record`, `incident` (safety kinds include `STOP_UNCONFIRMED`, `ADOPTED_POSITION_UNPROTECTED`, `STOP_MONITOR_UNRESPONSIVE`), `promotion_evaluation`, `llm_spend`
- [x] T022 [P] Add the `mode_state` transition trigger (PAPER→CO_PILOT→AUTONOMOUS, *→HALTED, HALTED→{PAPER,CO_PILOT}, CO_PILOT→PAPER) and a migration test in `tests/integration/test_mode_state_constraints.py` proving an illegal jump is refused at the database, not just in code (FR-001…FR-003)

### Venue facts — M0 verification gate

- [ ] T023 Write `docs/adr/0001-indodax-venue-facts.md` and update `specs/001-autonomous-crypto-trader/research.md` + a `venue_profile` seed with verified values for: native stop/OCO support and trigger semantics, all-in maker/taker fee (CFX component), whether PPh final 0.21% applies to both sides or sell only, per-pair `min_notional`/`step_size`/`tick_size`, and private order rate limits — verify against the live Indodax trade API docs and a read-only account probe, and set `native_stop_orders = false` until proven. **BLOCKS M3** (FR-067, R5). No implementation task may hard-code these numbers. **PARTIAL 2026-11-08**: documentation half done — `docs/adr/0001-indodax-venue-facts.md` + `infra/db/seeds/0001_venue_profile_indodax.sql` (native stop/OCO proven absent, min order, tick, `quantity_increment`, per-pair maker/taker, rate limits, PPh/PPN); `cfx_fee_pct` and buy-side tax left `NULL`/open because no official source states them. Remaining: (a) the read-only account probe (All-in-Fees detail + one real fill) to close those two fields, and (b) the **Lite/Pro boundary** — whether orders of Rp10.000–24.999 routed to Lite expose the same order types, fee percentages, `min_notional`, and create-order rate limit as Pro (see the ADR addendum). **T023 must not be marked `[x]`, and promotion to Live stays blocked, until both are verified.**

### Event bus, audit, auth

- [x] T024 Implement `packages/common/atlas_common/streams.py`: envelope publish/consume, consumer groups, `XAUTOCLAIM` of pending > 5 min, dead-letter after 3 failures, `MAXLEN` per stream per `contracts/event-bus.md`, with `tests/unit/test_streams.py` (idempotent redelivery)
- [x] T025 Implement `packages/common/atlas_common/hashchain.py` (`hash = sha256(prev_hash ‖ canonical_payload ‖ seq)`) and `tests/unit/test_hashchain.py` proving a mutated record breaks verification (FR-037, quickstart S11)
- [x] T026 Implement the append-only audit writer in `services/execution/atlas_execution/audit.py` used by every mutating path (orders, mode changes, config changes, kill switch), with `tests/integration/test_audit_append_only.py` asserting the app role cannot UPDATE or DELETE
- [x] T027 Implement `packages/schemas/atlas_schemas/verdict_sign.py`: canonical payload serialization + HMAC-SHA256 sign/verify over `{proposal_id, venue_id, pair, side, sized_quantity, stop_price, target_price, verdict_at, expires_at}` and `tests/unit/test_verdict_sign.py` where any field change, a 61 s age, and a truncated signature all fail verification (FR-062)
- [x] T028a [P] Write `tests/integration/test_auth_flow.py` failing first (FR-045, constitution VIII): session cookie carries `HttpOnly`, `Secure`, `SameSite=Strict` and expires after 8 h idle; a replayed TOTP code is refused; an expired session is refused on every route; `POST /auth/logout` invalidates the session server-side (the old cookie no longer authenticates); a passkey challenge is single-use and a replayed assertion fails; 5 failed logins lock the account for 15 min; and every login attempt (success, failure, lockout) plus every operator action writes one audit record. Confirmation strength: `POST /mode`, `PUT /risk/config`, `POST /promotion/confirm`, and `POST /halt/lift` are refused with `403 CONFIRM_REQUIRED` when `X-Atlas-Confirm` is absent, expired, already used, or minted for a different action or `params_hash`; `POST /killswitch` and `POST /positions/{id}/close` succeed with an authenticated session alone and **no** token, and `POST /positions/{id}/stop` does too (it is tighten-only); the same close succeeds while the account is locked out of new logins because an existing session stays valid; the Telegram control path (`contracts/telegram-control.md`) is never gated by the dashboard login lockout
- [x] T028b Implement `services/api-gateway/atlas_api/auth.py` to pass T028a: dashboard login + TOTP (mandatory) + optional passkey, `POST /auth/login`, `/auth/totp`, `/auth/passkey/begin` + `/auth/passkey/finish`, `/auth/logout`, `GET /auth/session`, server-side session revocation, and the single-use `X-Atlas-Confirm` token from `POST /confirm` (60 s TTL). The token guards exactly `POST /mode`, `PUT /risk/config`, `POST /promotion/confirm`, and `POST /halt/lift`; `POST /killswitch`, `POST /positions/{id}/close`, and `POST /positions/{id}/stop` are deliberately exempt and need only an authenticated session, because all three are emergency or exposure-reducing paths (FR-063, FR-071) and a second confirmation step there would add a failure mode to an exit (FR-045)
- [x] T029 [P] Implement `services/api-gateway/atlas_api/health.py` and `GET /health`, `GET /mode`, `GET /audit` read endpoints with the contract's error envelope, plus `tests/contract/test_api_read_endpoints.py`
- [x] T029f [P] Write `tests/contract/test_api_read_modules.py` first, covering T029a–T029e: every documented read route returns the contract shape, an unknown id returns `404` in the contract's error envelope, `/decisions/{id}?format=md` returns a downloadable bundle, raw prompts appear only to an authenticated operator, and no response body contains any secret-store value (FR-045, constitution VIII)
- [x] T029a [P] Implement `services/api-gateway/atlas_api/routes/positions.py` — `GET /positions?status=open|closed` with stop, target, `time_stop_at`, unrealised PnL, and per-venue rows aggregated to a portfolio view (FR-034)
- [x] T029b [P] Implement `services/api-gateway/atlas_api/routes/orders.py` — `GET /orders?status=&since=` with fills, fees, tax, reject reason, and `client_order_id` (FR-037)
- [x] T029c [P] Implement `services/api-gateway/atlas_api/routes/market.py` — `GET /market/pairs` and `GET /market/bars?pair=&tf=&from=&to=` with the 5 000-bar cap and the freshness `age_ms` field (FR-026)
- [x] T029d [P] Implement `services/api-gateway/atlas_api/routes/decisions.py` — `GET /decisions` (range + outcome filter) and `GET /decisions/{id}` with the full audit bundle and `?format=md`; this module is owned here, **not** by T091, which only adds the stub agent (FR-039, SC-007)
- [x] T029e [P] Implement `services/api-gateway/atlas_api/routes/incidents.py` — `GET /incidents?state=open` with severity and the automatic action taken (FR-061)

### Venue port and Indodax adapter (read-only half)

- [x] T030 Implement the `VenuePort` protocol in `packages/schemas/atlas_schemas/venue_port.py` with **no** withdraw/transfer/deposit method, and `tests/contract/test_venue_port.py` items 1–10 from `contracts/venue-adapter.md` running against recorded fixtures
- [x] T031 Implement `services/execution/atlas_execution/venues/indodax_public.py`: public REST (`pairs`, `tickers`, `order_book`, `recent_trades`, `bars`) + public WS stream with reconnect/backoff, mapping every failure onto the `VenueUnavailable`/`VenueRateLimited`/`VenueSchemaError` taxonomy (no vendor exception escapes)
- [x] T032 [P] Build the fixture set in `tests/fixtures/indodax/` (pairs, ticker, book, trades, bars, a 429, a malformed payload) with `tests/contract/test_indodax_public.py` asserting pair metadata parses to `PairInfo` with `min_notional` 10000 and that a truncated payload raises `VenueSchemaError`
- [x] T033 [P] Implement the token-bucket limiter per endpoint class (`public`/`private`/`order`) in `packages/common/atlas_common/ratelimit.py` sized from `venue_profile.rate_limit` with a Redis cross-process ceiling, plus a separate `stop` class reserving **2 create-order calls per second per pair and 8 venue-wide** for exits (see `contracts/stop-monitor.md` § 7 — the venue's 20 rps ceiling blocks trading for 5 s when exceeded, so exits never approach it), and `tests/unit/test_ratelimit.py`

### Market data and terminal skeleton

- [x] T034 Implement `services/ingest-market/atlas_market/ingest.py`: subscribe via T032, aggregate 1-minute bars idempotently on `(venue_id, pair, timeframe, ts)`, publish `atlas:market.bar`/`market.tick`, refresh `quote_snapshot` and `data_health` with `expected_interval_s` per source
- [x] T035 [P] Write `tests/integration/test_ingest_market.py`: replay fixtures into a test container, assert no duplicate bars on restart, `data_health.status` flips to `STALE` after the expected interval passes, and `atlas:market.bar` consumers see each bar exactly once
- [x] T036 Implement `apps/terminal-web/src/app/(dashboard)/page.tsx` skeleton: positions table, price chart (`lightweight-charts`), kill-switch button always visible in the first five tab stops, WCAG 2.1 AA focus order and contrast (FR-043), consuming the gateway WS
- [x] T037 [P] Implement `services/api-gateway/atlas_api/ws.py` (`/ws` topics market/positions/orders/cycles/health/incidents, 15 s heartbeat, drop `market.tick` first under pressure) with `tests/integration/test_ws_backpressure.py`

**Checkpoint (M0/M1)**: compose up, PAPER is the default, bars land in TimescaleDB, freshness is observable, terminal shows a live price, and T023 is either verified or explicitly recorded as `native_stop_orders = false`.

---

## Phase 3: Setup gap — CI quality gates for the whole repo

- [x] T038 [P] Add `tests/conftest.py` shared fixtures (ephemeral Postgres/Redis via compose, fake clock, signed-verdict factory, `SimulatedVenue`) so every later test is one file, not one harness
- [x] T039 [P] Add `docs/runbooks/halt.md`, `docs/runbooks/reconcile-mismatch.md`, `docs/runbooks/promotion.md`, and `docs/operator/incident-definitions.md` matching the safety-incident list in FR-061

---

## Phase 4: M1 completion — market-data correctness that M3 depends on

- [x] T040 [US1] Implement `services/feature-engine/atlas_features/staleness.py`: exponential-moving σ of 1-minute returns per pair, spread ratio vs the 24 h median, and a `Freshness` verdict object consumed by the Risk Engine, with `tests/unit/test_staleness.py` pinning 5σ and 3× to the spec's numbers (FR-027, FR-065)
- [x] T041 [US1] Implement the order-book walk + expected-price model in `packages/common/atlas_common/slippage.py` (`expected_price` from depth, `deviation_pct`) with `tests/unit/test_slippage.py` on recorded books, since FR-032's guard and FR-058's cost model both read it

---

## Phase 5: M2 — News and market intelligence pipeline (US3)

**Goal**: Free RSS sources become deduped, injection-screened, timestamped `news_item` rows plus `atlas:news.enriched`.

**Independent Test**: with the network stubbed, ≥3 feeds ingest into `news_item`, duplicates collapse, an instruction-like item is flagged but stored, and a downstream consumer can read a 24 h window without hitting a feed.

- [x] T042 [P] [US3] Write `services/ingest-news/atlas_news/feeds.py` with the allowlist from research R3 (CoinDesk, Cointelegraph, Decrypt, The Block, Bitcoin Magazine, CryptoSlate, Coinvestasi, OJK/Bappebti releases), per-feed `expected_interval_s`, ETag/Last-Modified caching, and **no** paid vendor path (FR-052)
- [x] T043 [P] [US3] Write `tests/unit/test_news_dedupe.py` failing first: canonical URL normalization + title SimHash within 24 h collapses a syndicated story into one row with `duplicate_of` set
- [x] T044 [US3] Implement `services/ingest-news/atlas_news/dedupe.py` to pass T043, plus the 24 h window query used by the UI and agents
- [x] T045 [P] [US3] Write `tests/unit/test_news_injection_screen.py` failing first: items containing imperative trading instructions ("buy now", "ignore previous instructions", "transfer funds", a prompt-injection tag of the form `system` in angle brackets) are flagged `injection_suspected=true`, still stored, and excluded from any text block handed to a model (FR-010, FR-012, constitution VI)
- [x] T046 [US3] Implement `services/ingest-news/atlas_news/injection.py` to pass T045, with the pattern list in one module and a versioned `screen_version` recorded per item
- [x] T047 [US3] Implement `services/ingest-news/atlas_news/worker.py`: poll → parse → dedupe → screen → insert `news_item` → publish `atlas:news.raw`; a single dead feed degrades to `data_health=STALE` for that source and never stops the loop (FR-011)
- [x] T048 [US3] Extend `services/feature-engine/atlas_features/news.py` to publish `atlas:news.enriched` with recency and relevance fields, deliberately **without** embeddings (pgvector tables are deferred to M6 per plan.md Complexity Tracking)
- [x] T049 [P] [US3] Write `tests/integration/test_news_pipeline.py`: three stubbed feeds, one flapping, assert bounded runtime (< 60 s), dedupe across feeds, `injection_suspected` persisted, and a `STALE` health row for the flapping feed
- [x] T050 [US3] Add `GET /news` (window filter, injection flag visible) to `services/api-gateway/atlas_api/routes/news.py` and a news panel in `apps/terminal-web/src/app/(dashboard)/news/page.tsx` that renders flagged items with a warning badge, never as instructions

**Checkpoint (M2)**: news is flowing, deduped, screened, and visible; no model has seen it yet (agents are M4).

---

## Phase 6: M3 — Risk Engine (US1)

**Goal**: A pure, deterministic gate that turns a proposal + snapshot into a signed, single-use, 60 s verdict or a recorded rejection.

**Independent Test**: quickstart S3, S4, S13 pass with the risk engine alone in a test process — no exchange, no Redis, no network.

- [x] T051 [P] [US1] Write `tests/unit/risk/test_rules_golden.py` failing first: a fixture snapshot produces `rule_results` in the fixed six-stage order with stable ids for all 23 documented rule ids from `contracts/decision-risk.md` (22 evaluated in the six stages plus the envelope-level `INVALID_OUTPUT`), so the audit diff never reorders
- [x] T052 [P] [US1] Write `tests/unit/risk/test_rule_min_order_exceeds_risk_budget.py` failing first (FR-051, SC-013): equity Rp100.000, risk Rp500, entry 1000, stop 900 (10% away) ⇒ reject `MIN_ORDER_EXCEEDS_RISK_BUDGET` because `10000 × 10% = Rp1.000 > Rp500`; same proposal on equity Rp5.000.000 ⇒ approved; and no configuration can widen the budget to pass it
- [x] T053 [P] [US1] Write `tests/unit/risk/test_rule_rr_below_min.py` failing first (FR-058, SC-010): net R:R is computed from the venue profile's fees + CFX + PPh + PPN + slippage, with the **exit leg priced taker** and the default round trip `taker_buy + taker_sell + PPh_sell`; 1.49:1 net is rejected, 1.51:1 approved, a profile with `tax_applies_to=SELL_ONLY` changes the computed `rr_net` in the expected direction (the test asserts the arithmetic, not just the verdict); a profile with `cfx_fee_pct IS NULL` and mode **Live** rejects with `COST_MODEL_INCOMPLETE` instead of assuming 0, while the same profile in **Paper** and **Backtest** approves with `cost_model_source=ASSUMED` (and that marker present on the `RR_BELOW_MIN` rule result); and a Live verdict is refused when the only non-NULL cost came from an `*_assumed` column
- [x] T053a [P] [US1] Write `tests/unit/risk/test_rule_news_only_evidence.py` failing first (FR-013): a proposal whose evidence is only `NEWS`/`SOCIAL` items at `source_tier == 3` is rejected with `NEWS_ONLY_EVIDENCE`; the same proposal is approved when one `BAR` evidence is present, when one tier-1 or tier-2 news item is present, or when the tier-3 items are merely additional support next to technical evidence; an empty evidence list rejects with `NEWS_ONLY_EVIDENCE` rather than passing silently
- [x] T054 [P] [US1] Write `tests/unit/risk/test_rule_stop_and_sizing.py` failing first: `NO_STOP_LOSS` when `stop_price` is null, `STOP_TOO_WIDE` beyond the configured band, sizing = `risk_budget / (entry - stop)` rounded **down** to `step_size`, `POSITION_SIZE_EXCEEDS_MAX` at 25% of equity, and `realised_risk_pct ≤ 0.5` reported on every approval (FR-023, FR-024, FR-025)
- [x] T055 [P] [US1] Write `tests/unit/risk/test_rule_portfolio_limits.py` failing first: asset exposure > 50%, total > 100%, daily loss ≥ 2%, drawdown ≥ 10%, and > 5 open positions each reject with their own rule id, with limits aggregated across two venues' positions (FR-024, FR-028)
- [x] T055a [P] [US1] Write `tests/unit/risk/test_rule_remaining_behaviour.py` failing first — behaviour, not presence, for every rule the golden test only names: `MARKET_NOT_SPOT` (derivatives or leveraged pair refused, FR-050), `VENUE_NOT_LIVE_ENABLED` (venue without an OJK licence and not live-enabled refused, FR-054), `MACRO_BLACKOUT` (proposal inside a configured macro window refused, and allowed 1 s after it ends), `COOLDOWN_ACTIVE` (a second entry on the same pair inside the cooldown refused, allowed after it), `ORDER_RATE_LIMIT` (the N+1th order inside the window refused with the observed count), `DUPLICATE_PROPOSAL` (same cycle + pair + side refused), and `INVALID_OUTPUT` (an unparseable model envelope yields a recorded rejection and no proposal, FR-017/FR-018). Each case asserts the rule id, the observed value, and the limit in `RuleResult`
- [x] T056 [US1] Implement `services/risk-engine/atlas_risk/rules/` (one module per rule group: `venue.py`, `freshness.py`, `evidence.py`, `structure.py`, `sizing.py`, `portfolio.py`, `operational.py`) to pass T051–T055, T053a, and T055a, each rule a pure `(snapshot, proposal, config, profile) -> RuleResult`; `evidence.py` owns `NEWS_ONLY_EVIDENCE`
- [x] T057 [US1] Implement `services/risk-engine/atlas_risk/engine.py`: fixed evaluation order with short-circuit, `primary_rule_id`, `input_hash`, `cost_breakdown`, `realised_risk_*`, and no I/O — `monkeypatch`ing `httpx`/`redis` in a test must still yield a verdict (constitution II)
- [x] T058 [US1] Implement `services/risk-engine/atlas_risk/verdict.py`: sign approved verdicts via T027, stamp `expires_at = verdict_at + 60 s`, refuse to sign when `risk_config_version` or `venue_profile_id` is stale, refuse with `COST_MODEL_INCOMPLETE` in **Live** while any cost component is `NULL`, fall back to the `*_assumed` columns in Paper/Backtest and stamp `cost_model_source` on the verdict and on each consuming `rule_results` entry, and write the verdict row in one transaction; `tests/unit/risk/test_verdict_issuance.py` asserts no signature exists on a rejection, that the exit leg is priced taker, and that round-trip friction is `taker_buy + taker_sell + PPh_sell`
- [x] T059a [P] [US1] Write `tests/unit/risk/test_breakers.py` failing first (FR-027, FR-065): spread > 3× 24 h median, 1-minute move > 5σ, quote older than 10 s, and `data_health` DOWN each produce the documented rule id with the observed value and limit in the result, and a breaker never blocks a reduce-only order
- [x] T059b [US1] Implement `services/risk-engine/atlas_risk/breaks.py` to pass T059a, one pure function per breaker with the numeric limits read from configuration, never hard-coded
- [x] T060 [US1] Implement `services/risk-engine/atlas_risk/baseline.py`: read `equity_baseline` for the current Asia/Jakarta day and stage, refuse a stale baseline instead of recomputing it, and open the 00:00 WIB row; `tests/unit/risk/test_daily_reset.py` covers a 23:59→00:01 transition and a stage change that re-seeds the high-water mark (FR-066)
- [x] T061 [P] [US1] Write `tests/unit/risk/test_management_actions.py`: `MOVE_STOP` that tightens is approved, that widens is rejected, `CLOSE` requires no verdict and no risk budget, and `TAKE_PROFIT`/scale-in/scale-out payloads fail schema validation outright (FR-020, SC-027)

---

## Phase 7: M3 — Execution, order path, and order-state integrity (US1)

**Goal**: `execution` is the only process that can mutate an order, it accepts only a live single-use verdict, and every abnormal order state resolves without ever double-submitting.

**Independent Test**: quickstart S5, S14, S16, S17 and the verdict-rejection matrix run against `SimulatedVenue` — the real venue stays untouched.

- [x] T062 [US1] Write the credential-isolation test first in `tests/security/test_credential_isolation.py` (FR-064, constitution I/VIII): assert `services/agents` and `services/api-gateway` images contain no venue key material, that the container env of `agents` and `api-gateway` contains no variable matching `ATLAS_VENUE_*` and no HMAC key, and that only `execution` may reach the order endpoints (egress allowlist in `infra/compose/docker-compose.yml`). The assertion is unconditional: the test fails whenever any `ATLAS_VENUE_*` variable is present in the `agents` env, whatever else the repository contains. Runs after T006 (it asserts on that file), so it is deliberately not marked parallel
- [x] T063 [US1] Make T062 pass: split env in `infra/compose/docker-compose.yml` (per-service secrets, `risk-engine` holds the HMAC key, `agents` holds only the LLM key), add the egress allowlist, and document the boundary in `docs/adr/0002-credential-boundary.md`
- [x] T064 [P] [US1] Write the verdict-rejection matrix first in `tests/integration/test_order_verdict_gate.py` (FR-062, SC-020): (a) no verdict at all ⇒ `VERDICT_UNKNOWN_ID`; (b) `expires_at` 1 s in the past ⇒ `VERDICT_EXPIRED`; (c) same verdict submitted twice ⇒ second is `VERDICT_CONSUMED`; (d) payload quantity doubled with the original signature ⇒ `VERDICT_PAYLOAD_MISMATCH`; (e) verdict for a different venue ⇒ `VERDICT_SIGNATURE_INVALID`. Every case must also write one audit record and produce **zero** venue requests
- [x] T065 [US1] Implement `services/execution/atlas_execution/gate.py` to pass T064: verify signature → check `expires_at` → atomically set `consumed_at` and insert the order → only then call `place_order`
- [x] T065a [P] [US1] Write `tests/integration/test_mode_gate.py` failing first (FR-001, FR-002, FR-063, SC-012): an entry order is refused with `MODE_NOT_TRADABLE` when the mode is `PAPER` and the venue is live-enabled, and when the mode is `HALTED`, each with one audit record and **zero** venue requests; a reduce-only close and a protective-stop placement still reach `SimulatedVenue` under both modes; and an unreadable/unknown mode never resolves to a tradable live mode
- [x] T065b [US1] Add the mode gate to `services/execution/atlas_execution/gate.py` to pass T065a: read `mode_state` in the same transaction that consumes the verdict, refuse entries with `MODE_NOT_TRADABLE`, and let orders whose `is_reduce_only` is computed from position state through (composes with T080)
- [ ] T066 [P] [US1] Write `tests/integration/test_single_writer.py` (FR-071, SC-026): a direct `place_order`/`cancel_order` call from a service other than `execution` is impossible (import boundary test) and every mutation path emits exactly one audit record; include an entry-cancel with an expired verdict ⇒ refused, and a reduce-only cancel ⇒ accepted
- [ ] T067 [US1] Implement `services/execution/atlas_execution/orders.py`: place/cancel/modify as the sole writer, `client_order_id = atlas-{cycle}-{proposal}-{attempt}`, `supports_order_modify=false` fallback to cancel-then-replace with the same lineage and no double-exposure window
- [ ] T068 [P] [US1] Write `tests/integration/test_order_state_machine.py` failing first: legal transitions per `data-model.md` advance, an illegal transition (`FILLED → CANCELED`) raises and is logged, and every transition is audited once
- [ ] T069 [US1] Implement `services/execution/atlas_execution/state_machine.py` with the enum including `UNKNOWN` as non-terminal, and the guard that only one `UNKNOWN` may exist per venue
- [ ] T070 [P] [US1] Write `tests/integration/test_partial_fill.py` failing first (FR-068, SC-023): a 40% fill ⇒ position quantity = filled quantity, stop order re-sized to it within one cycle, remainder cancelled after 45 s, and no committed snapshot exists where the position is open with a stop quantity of zero
- [ ] T071 [US1] Implement partial-fill handling in `services/execution/atlas_execution/fills.py` + `position_sync.py` (stop resize on every fill/reduction, remainder timer)
- [ ] T072 [P] [US1] Write `tests/integration/test_unknown_order.py` failing first (FR-069, SC-024): a `place_order` timeout ⇒ status `UNKNOWN`, new entries blocked on that venue, resolution via `get_order(client_order_id=…)` only, the recorded request log contains exactly one `place_order`, and 3 failed resolutions ⇒ `HALTED` + `SAFETY` incident + alert
- [ ] T073 [US1] Implement `services/execution/atlas_execution/recovery.py`: UNKNOWN resolution loop with backoff, venue freeze flag, and the 3-attempt escalation
- [ ] T074 [P] [US1] Write `tests/integration/test_close_escalation.py` failing first (FR-070, SC-025): two rejected closes ⇒ operator alert over the remote channel, new entries stopped, third attempt becomes a market order inside the slippage guard, and the position is never reported closed while it is open
- [ ] T075 [US1] Implement `services/execution/atlas_execution/closing.py` (retry ladder, alert at second failure, market fallback bounded by the slippage guard, entries blocked until flat)
- [ ] T075a [P] [US1] Write `tests/integration/test_time_stop.py` failing first (FR-057): a position older than `max_holding_time` (default 5 days) is closed automatically by a `ManagementAction(type=TIME_STOP)` that needs no verdict and no risk budget, records `time_stop_at` and `close_reason=TIME_STOP`, and is emitted once and only once per position; a position inside the window is not touched, an earlier exit rule wins, and the close remains reduce-only so `HALTED`, an active breaker, or an exhausted budget cannot block it
- [ ] T075b [US1] Implement the holding-time sweeper in `services/execution/atlas_execution/time_stop.py`: compare `time_stop_at` against the synchronized clock every cycle, emit the `TIME_STOP` management action idempotently under the leader lock, and hand the position to `closing.py`
- [ ] T071a [P] [US1] Write `tests/integration/test_stop_unconfirmed.py` failing first (FR-033, FR-063, FR-071): an entry fills but the venue rejects the protective stop (and, separately, never acknowledges it) ⇒ the position is closed by a reduce-only order within one cycle, with one `SAFETY` audit record, a `STOP_UNCONFIRMED` incident, and an operator alert; the close attempt is never blocked while the mode is `HALTED`, a breaker is active, or the LLM budget cap is exhausted; if the close itself fails the test follows the FR-070 escalation ladder; the position must not stay `OPEN` + `UNCONFIRMED` beyond one cycle (data-model invariant 11)
- [ ] T071b [US1] Implement stop-confirmation tracking in `services/execution/atlas_execution/fills.py` + `closing.py` to pass T071a: **requires T075, T075a, T075b** (they own the reduce-only close path this task routes into; no partial or stubbed close path may exist here). After a fill, a stop that is rejected or unacknowledged marks the position `stop_confirmation = UNCONFIRMED`, emits the `SAFETY` incident, opens the alert, and hands the position to `closing.py`; the protective-stop acceptance path itself stays in T087 and this task only decides that no confirmed stop exists
- [ ] T076 [P] [US1] Write `tests/integration/test_reconcile_cross_venue.py` failing first (FR-034, FR-055, SC-016): two venues with divergent positions ⇒ portfolio view flags the mismatch, new entries blocked, per-venue rows reconciled independently, and the global sums equal the sum of per-venue values
- [ ] T077 [US1] Implement `services/execution/atlas_execution/reconcile.py`: 5-minute full reconcile of orders/positions/balances per venue, portfolio aggregation, `atlas:reconcile.result`, and the `RECONCILIATION_MISMATCH` input to the Risk Engine. A position discovered at the venue and adopted into the database MUST be handed to the stop monitor as stop-less on adoption and MUST have a protective stop by the end of the monitor's first cycle; if it does not, the system goes `HALTED` with a `SAFETY/ADOPTED_POSITION_UNPROTECTED` incident and an operator alert (FR-067, SC-002)
- [ ] T078 [P] [US1] Implement the venue-key permission probe in `services/execution/atlas_execution/permissions.py` with `tests/integration/test_key_permissions.py`: withdrawals enabled or IP whitelist inactive ⇒ refuse to start in a Live stage and emit `SEV1` (FR-036, constitution VIII)

---

## Phase 8: M3 — Fail-safe control, stop monitor, budget (US2)

**Goal**: Exits always work, the kill switch works with agents dead, a dead stop monitor is itself an incident, and the spend cap throttles entries without ever blocking an exit.

**Independent Test**: quickstart S7, S6, S14, S15 pass with `execution` + `stop-monitor` + gateway running and no agents.

- [ ] T079 [P] [US2] Write `tests/integration/test_exit_never_blocked.py` failing first (FR-063, SC-021): with the system `HALTED`, with a circuit breaker active, with the LLM cap exhausted, and with an `UNKNOWN` order present, a full close and a protective-stop placement both reach the venue and fill in the simulator; simultaneously zero new BUY orders are accepted
- [ ] T080 [US2] Implement the reduce-only bypass in `services/execution/atlas_execution/gate.py` (`is_reduce_only` computed from position state, not from the caller's claim) so that T079 passes while T064's rejections still hold
- [ ] T080a [P] [US2] Write `tests/security/test_stream_producer_allowlist.py` failing first (FR-064, constitution I/VIII): an envelope published to `atlas:order.signed` or `atlas:stopmonitor.action` by a producer that is not on that stream's allowlist is dropped by the consumer, counted in `atlas_bus_rejected_total`, and produces exactly one `SAFETY` audit record; a forged envelope that carries a copied signature is still rejected by HMAC; and `execution` never calls `place_order` for either case
- [ ] T080b [US2] Implement the consumer-side producer allowlist for every protected stream in `packages/common/atlas_common/streams.py` (table from `contracts/event-bus.md` § Producer allowlist) plus per-service Redis ACL users in `infra/compose/docker-compose.yml` (`+xadd` limited to a service's own streams, `+xreadgroup`/`+xclaim` to the streams it consumes), with a compose-network test that a service cannot `XADD` to a stream it does not produce
- [ ] T081 [P] [US2] Write `tests/integration/test_killswitch.py` failing first (FR-005, FR-008, constitution V): kill switch publishes to Redis before any DB write, takes effect with `agents` stopped, is idempotent, needs only an authenticated session (no `X-Atlas-Confirm`), still works while the operator account is locked out of new dashboard logins, the Telegram `/halt` path is independent of the dashboard lockout and of the gateway HTTP session, and only an authenticated operator can lift `HALTED`
- [ ] T082 [US2] Implement `services/api-gateway/atlas_api/killswitch.py` + `POST /killswitch` (session only, no confirm token) and `POST /halt/lift` (session + confirm token), plus the Telegram bridge command surface from `contracts/telegram-control.md` (allowlist + TOTP, 60 s replay window, `update_id` idempotency, independent of the dashboard login lockout)
- [ ] T082a [P] [US2] Write `tests/integration/test_operator_mode.py` failing first (FR-003, SC-012): `POST /mode` is refused with `403 CONFIRM_REQUIRED` when the token is missing, expired, reused, or bound to another action or `params_hash`, and succeeds only with a valid token; it records operator identity, reason, and `evidence_id`, refuses `PAPER → AUTONOMOUS` at the API layer with `MODE_TRANSITION_INVALID` even when a passing evaluation exists, and always allows demotion; the request writes its audit record before returning success
- [ ] T082b [US2] Implement `services/api-gateway/atlas_api/routes/mode.py` + `POST /mode` to pass T082a: validate against the database trigger (T022), never promote without a fresh passing `promotion_evaluation` and `POST /promotion/confirm`, audit before returning, and publish `atlas:mode.changed`
- [ ] T082c [P] [US1] Write `tests/integration/test_operator_close.py` failing first (FR-063, FR-071, SC-021): `POST /positions/{id}/close` is forwarded to Execution as a `ManagementAction(type=CLOSE)` — the gateway makes no venue call and holds no credentials — it needs only an authenticated session and must **not** require `X-Atlas-Confirm` (`403 CONFIRM_REQUIRED` there is a failure), it is processed while `HALTED`, while a breaker is active, and while the spend cap is exhausted, it still works while the account is locked out of new logins, and it writes exactly one audit record
- [ ] T082d [US1] Implement `POST /positions/{id}/close` by extending `services/api-gateway/atlas_api/routes/positions.py` (after T029a) to publish the `ManagementAction` and stream the resulting `position.update` back to the operator
- [ ] T082e [P] [US1] Write `tests/integration/test_operator_stop.py` failing first (FR-020, FR-063, SC-027): `POST /positions/{id}/stop` is forwarded as a `ProtectiveStopRequest` with a monotonic `generation`; it is accepted with an authenticated session alone and a `403 CONFIRM_REQUIRED` there is a failure (no `X-Atlas-Confirm` for exits); a stop that widens (or equals the current stop) is still refused with `409 STOP_NOT_TIGHTENING` and causes no venue request; a tightening stop is accepted under `HALTED`; and the response reports the venue's answer, not an optimistic success
- [ ] T082f [US1] Implement `POST /positions/{id}/stop` in `services/api-gateway/atlas_api/routes/position_actions.py`: validate tighten-only against the stored entry/stop before forwarding, hand the request to Execution (never to a venue), and audit the operator identity with the old and new stop
- [ ] T083 [P] [US2] Write `tests/chaos/test_stop_monitor_heartbeat.py` failing first (FR-067, SC-022): stop the monitor process ⇒ `HALTED` within 5 s of the 30 s threshold, a `SAFETY/STOP_MONITOR_UNRESPONSIVE` incident, exactly one close attempt per open position, an operator alert, and restarting the monitor does **not** auto-lift `HALTED`
- [ ] T086 [P] [US2] Write `tests/contract/test_protective_stop.py` (from `contracts/stop-monitor.md` §6): missing stop re-placed within one cycle, a wider stop rejected and recorded, a stale `generation` ignored without touching the venue, no orders emitted when there are no positions, and an adopted reconcile position without a stop forcing `HALTED` when the first-cycle placement fails
- [ ] T087 [US2] Implement the protective-order acceptance path in `services/execution/atlas_execution/execute.py` (accept `ProtectiveStopRequest` without a verdict, enforce quantity ≤ open quantity and risk-tightening only, return a typed rejection the monitor turns into an incident). Placed before T084 on purpose: the monitor cannot be implemented against a path that does not exist. It also covers the reconcile-adoption case from T077 (stop-less adopted position ⇒ protected within one cycle, otherwise `HALTED` + `SAFETY/ADOPTED_POSITION_UNPROTECTED`)
- [ ] T087a [US2] Write `tests/integration/test_stop_burst_rate.py` failing first: a burst of protective stops and `CLOSE` requests across several pairs (a venue-wide drop, the realistic worst case) never exceeds the stop/exit budget of **2 create-order calls per second per pair and 8 per second venue-wide**, stays far below the venue's 20 rps per account per pair, and never provokes the 5-second trading block; a simulated venue `429` triggers exponential backoff with jitter and one incident rather than a retry storm, and a queued exit that cannot be sent within the budget escalates to the operator instead of silently waiting
- [ ] T084 [US2] Implement `services/stop-monitor/atlas_stopmon/monitor.py`: 10 s cycle over open positions, protective-stop presence/price/quantity checks, `ProtectiveStopRequest` emission with monotonic `generation`, and `stop_monitor_seen_at` updates — no credentials, no BUY path. **Requires T087 and T086** (it calls that acceptance path and is proven by that contract test)
- [ ] T085 [US2] Implement the watchdog in `services/api-gateway/atlas_api/watchdog.py`: read `stop_monitor_heartbeat`, evaluate every 5 s, force `HALTED` + `EXITS_ONLY` kill switch + incident + alert on staleness, and never auto-recover
- [ ] T088 [P] [US2] Write `tests/integration/test_budget_cap.py` failing first (FR-052, SC-014, SC-017): spending Rp5.000 in a day ⇒ `THROTTLED_ENTRIES` stage, `BUDGET_BLOCKED` on new entries, stops and exits unaffected; crossing Rp250.000 in the month ⇒ hard block; the dashboard endpoint reports `{today, cap, stage}` in ≤ 2 s
- [ ] T089 [US2] Implement `services/api-gateway/atlas_api/budget.py` + `services/risk-engine/atlas_risk/budget.py`: `llm_spend` ledger (day/month), pre-call reservation, degrade ladder `reduce_frequency → THROTTLED_ENTRIES → BUDGET_BLOCKED`, and `GET /spend`; the ledger is exercised with synthetic spend because the LLM client itself is M4
- [ ] T090 [P] [US2] Write `tests/chaos/test_fail_safe_defaults.py`: stale data, venue 5xx, malformed venue payload, unreadable `mode_state`, and a Redis `XADD` OOM each resolve to `HALTED`/no-new-positions and never to "keep trading" (constitution III)

---

## Phase 9: M3 — Paper loop, traceability, and operator surface (US1, US4, US5)

**Goal**: A proposal can travel the whole path with a stubbed agent, and the whole path is replayable and visible.

**Independent Test**: quickstart S1, S2, S6, S8, S9, S10 pass end-to-end in compose with `ATLAS_MODE=PAPER`.

- [ ] T091 [US1] Implement `services/agents/atlas_agents/stub.py`: a deterministic stub agent that emits schema-valid proposals from fixtures (so M3 is testable without M4), rejecting any proposal that fails T015 validation with a recorded `schema_error`. The `/decisions` route is **not** built here — it belongs to T029d
- [ ] T092 [US1] Wire the paper loop in `services/agents/atlas_agents/loop.py`: `IDLE → GATHER → ANALYSE → PROPOSE → REVIEW → EMIT → COOLDOWN`, leader lock `atlas:leader:cycle` (`NX PX 900000`, 60 s renew), skipped cycles recorded as `SKIPPED`, 15-minute cadence with a hard 180 s cycle cap (FR-014, R15)
- [ ] T093 [P] [US5] Implement `services/execution/atlas_execution/decision_record.py`: write the full bundle (prompts stubbed at M3, `model_meta`, `raw_outputs`, `verdict_id`, orders, fills, `code_version`, `config_hash`, three snapshot hashes) with `tests/integration/test_decision_record.py`
- [ ] T094 [US5] Implement `services/risk-engine/atlas_tools/replay.py` + `tests/integration/test_replay.py`: re-run the Risk Engine from a stored `decision_record` and assert byte-identical verdict, `rule_results`, and signed-order payload (FR-039, quickstart S10)
- [ ] T095 [P] [US4] Implement the terminal panels `apps/terminal-web/src/app/(dashboard)/{positions,orders,audit,spend}/page.tsx` with a keyboard-first command palette, the kill switch visible on every route, and WCAG 2.1 AA contrast/focus checks in `tests/e2e/terminal.a11y.spec.ts` (FR-041…FR-044)
- [ ] T096 [P] [US4] Write `tests/e2e/terminal-latency.spec.ts` (quickstart S12): synthetic tick → painted price ≤ 1 s on the compose network, no layout shift, kill switch reachable within five Tab presses
- [ ] T097 [US1] Implement `GET /promotion/evaluate` and the FR-059 criterion checker in `services/api-gateway/atlas_api/routes/promotion.py` with `tests/integration/test_promotion_gate.py`: a failing criterion is named in the response, promotion without a passing evaluation < 24 h is refused, and no auto-promotion path exists (the Co-pilot/Live stage itself is M7 — this task only makes the evaluation honest). The gate also refuses promotion unless the runtime database identity is a non-owner role with only SELECT/INSERT on the append-only tables, proven by `tests/integration/test_db_role_privileges.py` (research.md R17)
- [ ] T097a [P] [US1] Write `tests/integration/test_promotion_confirm.py` failing first (FR-003, SC-018): `POST /promotion/confirm` is refused without a re-authentication (TOTP or passkey) within the preceding 5 minutes; it records the operator identity, the timestamp, and the evaluation id in the audit log before returning success; it refuses an evaluation that is older than 24 h, already used, or has any failing criterion; and no other endpoint or code path can move the mode to a Live stage; the same fixture asserts the promotion ran under the non-owner runtime role (research.md R17)
- [ ] T097b [US1] Implement `POST /promotion/confirm` in `services/api-gateway/atlas_api/routes/promotion.py` to pass T097a: mandatory step-up re-auth, single use per evaluation id, audit-before-success, and a promotion that is void unless this confirmation exists (the Co-pilot/Live stage itself stays M7)
- [ ] T098 [US1] Add `GET /risk/config` + `PUT /risk/config` (audited, confirm-token, operator-only) in `services/api-gateway/atlas_api/routes/risk_config.py` and `tests/integration/test_risk_config_audit.py` proving an agent or LLM path cannot change limits (FR-029)

---

## Phase 10: Polish (M3 exit criteria)

- [ ] T099 [P] Run `quickstart.md` S1–S11 + S13–S18 and record the evidence file `docs/operator/m3-validation.md` (with the S12 screenshot)
- [ ] T100 [P] Chaos suite wiring in `tests/chaos/` for the M3 set: stale data, venue timeout, LLM timeout stub, invalid model output, rejected order, partial fill, stop placement failure, stop-monitor kill, `UNKNOWN` resolution, repeated close failure (constitution IX)
- [ ] T101 Execute the 72-hour paper run per `quickstart.md` § M3 exit criteria and record the evidence in `docs/operator/m3-paper-run.md`: zero orders without a stop, every cycle has a `decision_record`, daily LLM spend ≤ Rp5.000, reboot resumes `PAPER` with reconciled state
- [ ] T102 [P] Add Prometheus counters/histograms for verdict latency, order ack, cycle duration, stop-monitor heartbeat age, UNKNOWN count, and budget stage in `infra/observability/`, with the three alert rules for safety incidents
- [ ] T103 Write `docs/operator/m4-handoff.md`: exactly what M4 (agents v1) may assume — the stub interface, the verdict contract, the budget ledger, and the fact that no LLM has yet touched a live path

---

## Mandatory Test Coverage (traceability)

| Required test | Task | Spec |
| --- | --- | --- |
| Order rejected without verdict | T064(a) | FR-062, SC-020 |
| Verdict expired / consumed / tampered | T064(b)(c)(d)(e) | FR-062, FR-071 |
| Credential isolation (agents have no order path) | T062, T063 | FR-064, constitution I/VIII |
| Stop monitor + heartbeat watchdog | T083, T084, T085, T086 | FR-067, SC-022 |
| Partial fill keeps a sized stop | T070, T071 | FR-068, SC-023 |
| `UNKNOWN` order: freeze, query, no resubmit | T072, T073 | FR-069, SC-024 |
| Repeated close failure escalates | T074, T075 | FR-070, SC-025 |
| Cross-venue reconciliation | T076, T077 | FR-034, FR-055, SC-016 |
| Mode gate on entry orders (`MODE_NOT_TRADABLE`) | T065a, T065b | FR-001, FR-002, SC-012 |
| Mode default / invalid env value resolves to Paper | T012a, T012b | FR-002, SC-012 |
| Bus producer allowlist + SAFETY audit | T080a, T080b | FR-064, constitution I/VIII |
| Operator close and stop routed through Execution | T082c, T082d, T082e, T082f | FR-063, FR-071, SC-021, SC-027 |
| Promotion requires step-up re-auth confirmation | T097a, T097b | FR-003, SC-018 |
| Exit burst stays under the venue's order-rate ceiling | T087a | FR-070, SC-024 |
| Assumed costs usable in Paper but never in Live | T053, T058 | FR-058, FR-059 |
| Read endpoints for every terminal panel | T029a–T029f | FR-034, FR-037, FR-039, FR-045, FR-061 |
| Adopted reconcile position gets a stop | T077, T087, T086 | FR-067, SC-002 |
| Exits unblocked under Halted/breaker/budget | T079, T080 | FR-063, SC-021 |
| Budget cap + auto-throttle | T088, T089 | FR-052, SC-014/017 |
| FR-051 minimum order vs risk budget | T052 | FR-051, SC-013 |
| FR-058 R:R net of costs | T053 | FR-058, SC-010 |
| Single writer for order mutations | T066 | FR-071, SC-026 |
| Management actions limited to MOVE_STOP/CLOSE | T061 | FR-020, SC-027 |
| Auth flow: cookie attrs, TOTP replay, lockout, passkey single-use, server-side logout, confirm-token scope | T028a, T028b | FR-045, FR-063, FR-071, constitution VIII |
| Rules missing behavioural coverage (`MARKET_NOT_SPOT`, `VENUE_NOT_LIVE_ENABLED`, `MACRO_BLACKOUT`, `COOLDOWN_ACTIVE`, `ORDER_RATE_LIMIT`, `DUPLICATE_PROPOSAL`, `INVALID_OUTPUT`) | T055a | FR-017, FR-018, FR-050, FR-054, FR-065 |
| `NEWS_ONLY_EVIDENCE` (tier-3 news alone cannot open a position) | T053a, T056 | FR-013 |
| Stop unconfirmed after a fill ⇒ immediate reduce-only close | T071a, T071b | FR-033, FR-063, FR-071 |
| Maximum holding time closes the position (`TIME_STOP`) | T075a, T075b | FR-057 |

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1 (Setup)**: no dependencies.
- **Phase 2 (Foundational)**: needs Phase 1. **T028a/T028b precede every operator endpoint** (`T029a`–`T029f`, `T082a`–`T082f`, `T097a`/`T097b`), because each mutating route is unusable without the session, TOTP, and `X-Atlas-Confirm` layer they provide. **T023 is a hard M3 gate** — T062 onward may be written against `native_stop_orders = false`, but M3 cannot be declared done until T023 records verified values.
- **Phase 3 (CI gaps)**: needs T002/T005 only; can run alongside Phase 2.
- **Phase 4 (M1 correctness)**: needs T034 (bars) and T040 depends on T033.
- **Phase 5 (M2 news)**: needs Phase 2 (streams, DB, health).
- **Phase 6 (Risk Engine)**: needs T013–T017 (schemas), T040, T041, T018–T022 (tables), T023 (cost model).
- **Phase 7 (Execution)**: needs Phase 6 (verdict issuance) and T030–T033 (venue port + limiter). T062 asserts on `infra/compose/docker-compose.yml`, so it runs **after** T006 and is not `[P]`.
- **Phase 8 (Fail-safe/stop monitor/budget)**: needs T067, T069 (state machine) for T080; T080a/T080b additionally need T024 (streams). **T087 precedes T084 in this file because the monitor calls that acceptance path**; T084 requires T086 and T087. T089 is independent of Phase 6 but its verdict wiring needs T057.
- **Phase 9 (Paper loop)**: needs Phases 6, 7, 8. T095 additionally needs T029a–T029f (it renders the data those modules serve); T097a/T097b extend T097 and must land before M7 is discussed.
- **Phase 10 (Polish)**: needs everything above.

### Critical path

`T013–T017 → T018–T022 → T023 → T051–T061 → T064/T065 → T065a/T065b → T067/T069 → T070–T077 → T079/T080 → T080a/T080b → T083 → T086 → T087 → T084/T085 → T092 → T082a–T082f → T097a/T097b → T095 → T099–T101`

### Parallel tracks (after Phase 2)

| Track | Tasks | Note |
| --- | --- | --- |
| A — Risk Engine | T051–T061, T053a, T055a | pure code; usable the moment T023 lands |
| B — Execution core | T062–T078, T071a, T071b, T075a, T075b | depends on A for verdict issuance only |
| C — Fail-safe + stop monitor | T079–T087, T090 | T083 can be authored against T087's contract in parallel with B |
| D — News (M2) | T042–T050 | fully parallel to A–C |
| E — Terminal | T029a–T029f, T036, T037, T095, T096 | T029a–T029f first; T095 renders what they serve |
| F — Observability/docs | T039, T102, T103 | parallel |

### Within each phase

Tests first (they must fail before implementation — constitution IX), then models, then services, then endpoints/integration. `[P]` tasks in the same phase touch different files and can be claimed simultaneously.

---

## Parallel Example: M3 order-path integrity

```bash
# Three independent first-failing test files, all authored before implementation:
Task: "tests/integration/test_order_verdict_gate.py — rejection matrix (T064)"
Task: "tests/security/test_credential_isolation.py — no agent→order path (T062)"
Task: "tests/chaos/test_stop_monitor_heartbeat.py — watchdog escalation (T083)"
```

```bash
# Then the three implementations can proceed in parallel:
Task: "services/execution/atlas_execution/gate.py (T065)"
Task: "infra/compose separation + docs/adr/0002 (T063)"
Task: "services/stop-monitor/atlas_stopmon/monitor.py (T084)"
```

---

## Implementation Strategy

### MVP (M0–M3 floor)

1. Phase 1 → Phase 2 (T023 included).
2. Phase 6 (Risk Engine) with fixtures only — this is where the system's value lives and it needs no venue.
3. Phase 7 (Execution) against `SimulatedVenue`.
4. Phase 8 (Fail-safe, stop monitor, budget).
5. **STOP and VALIDATE**: quickstart S3, S4, S5, S7, S13–S18.
6. Phase 9 wiring + Phase 10 evidence.

### Explicitly out of scope here

Agents v1 and real LLM calls (M4), backtest lab (M5), embeddings/pgvector tables, derivatives and on-chain (M6), Co-pilot and Autonomous live stages (M7–M8). T091's stub agent exists only so M3 is testable; it must not grow into M4.

---

## Notes

- **Live promotion is blocked on data, not code.** Two items must close before `mode_state`
  can reach a Live-enabled stage: T023's account probe (CFX percentage, tax composition,
  buy-side tax) and the Lite/Pro boundary verification (orders of Rp10.000-24.999). `T097`
  must surface both as failing criteria rather than assuming them, and any Paper track record
  whose verdicts carry `cost_model_source = ASSUMED` is not evidence for enabling Live.
- **Task ids are stable; physical order inside a phase is the execution order.** Suffixed ids (`T012a`, `T029a`–`T029f`, `T059a`/`T059b`, `T065a`/`T065b`, `T080a`/`T080b`, `T082a`–`T082f`, `T097a`/`T097b`) were added without renumbering the existing 103 tasks, so a phase's bullets are meant to be read top to bottom, not sorted by id.
- `[P]` = different files, no unfinished dependency.
- Test-first deliberately overrides the suffix order in one place: `T029f` is written before `T029a`–`T029e`. `T071a`/`T071b` sit physically after `T075b` on purpose — they route into the reduce-only close path that `T075`/`T075a`/`T075b` build, so the close path is never a stub. Read the dependency text, not the letter.
- Gateway endpoints are verified by `tests/integration/`; `tests/contract/` exists only for the venue port (`T030`, `T032`, `T087`) and must not be used for HTTP route tests.
- Every test task states the requirement it proves, and the requirement id appears verbatim in the assertion message so a failure names the rule.
- Venue facts live in `venue_profile`; no task may hard-code fees, taxes, minimums, or rate limits.
- Commit per task or per small group; keep `spec.md`, `plan.md`, and `data-model.md` in sync if a task changes a contract.
- **M0 checkboxes are a branch-wide delivery claim, not a claim about the diff that flips them.** `T001`–`T003` and `T005`–`T009` were delivered by `fee9d3b` (M0 tooling, compose stack, VPS units, credential render path), `T010`–`T012b` by `2fef8eb` (money, clock, mode default, log redaction), and `T004` by `565b862` (lockfile) plus `23344e7` (Next scaffold). A reader verifying a `[X]` on this page should follow the named commit, not the commit that flips the box; `git log --oneline` reconstructs the whole sequence.
