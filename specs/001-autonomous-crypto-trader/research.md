# Phase 0 Research — ATLAS

All NEEDS CLARIFICATION items from the plan's Technical Context are resolved below. Every
entry is a decision with its rationale and the alternatives that were rejected. Verified
external facts carry the source and the date they were checked; anything that could drift
(venue fees, API limits, minimum order size) is additionally marked **re-verify at M0** and
MUST live in the `venue_profile` row, never in code.

---

## R1. LLM provider and routing

**Decision**: One internal `LLMClient` interface (OpenAI-compatible `/chat/completions`
with configurable `base_url`), two model tiers resolved by config:

| Tier | Used by | Class of model | Planning cost assumption |
| --- | --- | --- | --- |
| `cheap` | news classification, summarization, feature tagging | Gemini Flash-Lite / GPT-4o-mini class | $0.15/M input, $0.60/M output |
| `strong` | Strategist only, and only on candidate setups (≤ 6 calls/day) | Gemini 2.5 Pro / GPT-4.1-mini class | ~$0.50/M input, $1.50/M output |

Prompt budget: ≤ 6k input + 1k output per monitored cycle for `cheap`; ≤ 20k input + 2k
output per Strategist call. 96 cycles/day × 8k tokens ≈ $0.15/day, plus ≤ 6 Strategist
calls ≈ $0.12/day ⇒ ≈ Rp4.500/day at Rp16.700/USD, inside the Rp5.000/day cap.

**Rationale**: The cap is enforced by a spend ledger in Postgres (`llm_spend`), checked
*before* the call and reconciled after with actual usage; exceeding it degrades (FR-052),
never errors silently. Provider neutrality keeps the door open when a cheaper model
appears, which the Rp250.000/month envelope will eventually require.

**Alternatives considered**: (a) One strong model for everything — breaks the daily cap
within ~15 cycles; (b) local model on the same VPS — 4 GB RAM is already committed to
Postgres/Redis/Next.js, and a 7B model produces unusable structured decisions; (c) hosted
agent framework — adds a dependency and hides prompts, violating Principle VII.

---

## R2. Market data providers

| Provider | Data | Auth | Rate limit (as documented) | Cadence | Role |
| --- | --- | --- | --- | --- | --- |
| Indodax public REST (`indodax.com/api/*`: `summaries`, `ticker`, `trades`, `depth`, `charts/trades`) | IDR pairs: ticker, trades, depth, OHLC | none | undocumented; treat as ≤ 1 req/s per endpoint, honor `429`/`Retry-After` | 15 s ticker/depth, 60 s trades backfill | Primary |
| Indodax public WebSocket (`wss://ws3.indodax.com/ws/`) | trade + order book stream | none | connection-based | continuous | Primary streaming |
| Indodax Trade API 2.0 (`tapi.indodax.com`) | order history, fills | API key | **re-verify at M0** | on demand | Reconciliation |
| CoinGecko free (demo key) | global mcap, BTC dominance, cross-venue price sanity | demo API key | 30 calls/min, ~10k calls/month | 5 min | Secondary / sanity |
| Exchange public REST of the *other* OJK venues | reference prices | none | per venue | 5 min | Read-only comparison |

**Decision**: Indodax is the only authoritative source for anything that can become an
order. Cross-venue prices are read-only context and may trigger a *stale/mispricing*
breaker but never a trade on another venue without that venue's adapter.

**Rationale**: One authoritative book per tradable venue means FR-016/FR-021 freshness and
slippage checks have an unambiguous reference. CoinGecko free tier is enough for breadth
(market regime) at 5-minute cadence.

**Alternatives considered**: ccxt unified tickers (hides per-venue quirks and adds a large
dependency — see R9); paid aggregators (Kaiko/CCData) — excluded by FR-052.

**Re-verify at M0**: exact public/private rate limits, WebSocket channel names and
heartbeat behavior, `depth` payload shape, and whether `tapi` requires a separate key scope.

---

## R3. News sources (free only)

**Decision**: Direct RSS from a curated allowlist, polled every 60–120 s with
`If-Modified-Since`/`ETag`: CoinDesk, Cointelegraph, Decrypt, The Block, Bitcoin Magazine,
CryptoSlate, plus 1–2 Indonesian outlets (Coinvestasi; OJK/Bappebti press releases for
regulatory events). No API keys, no aggregator in the critical path.

Pipeline: fetch → parse (`feedparser`) → canonicalize URL → dedupe (URL hash + title
SimHash + 24 h window) → entity/ticker tagging → language detect → embed (384-dim,
`sentence-transformers` MiniLM, CPU) → injection screen → store → `news.raw` event.

**Rationale**: RSS is the only free source with a stable contract and no quota. Two
independent defenses (dedupe, embedding novelty) keep the 15-min cycle from re-reading the
same story 20 times, which is what actually burns the LLM budget.

**Alternatives considered**: CryptoPanic free tier (100–1000 req/day, key, aggregator
outage becomes our outage); X/Twitter (no free tier); third-party free JSON aggregators
(`crypto-news.live`, `cryptocurrency.cv`) — no SLA, unknown provenance; usable only as an
optional supplement, never as the sole feed.

**Injection defense (Principle VI)**: every item is wrapped as
`<untrusted_news id=… source=… published_at=…>…</untrusted_news>`, a regex/pattern
pre-screen flags instruction-like content ("ignore previous", "system:", "you must",
URLs with credentials), and flagged items are stored but excluded from prompts.

---

## R4. Macro and on-chain sources (M6 gate)

| Source | Data | Auth | Limit | Cadence |
| --- | --- | --- | --- | --- |
| FRED API v1 (`api.stlouisfed.org`) | DGS10, DTWEXBGS (dollar index), DFF | free key | ~120 req/min (v1) | 6 h |
| CBOE CSV | VIX | none | static file | 6 h |
| alternative.me `fng` | Fear & Greed index | none | ~1 req/min, 1/day data | 1 h |
| mempool.space REST | BTC mempool, fees, blocks | none | ~1 req/s polite | 5 min |
| Blockstream Esplora | BTC address/tx lookups | none | ~1 req/s polite | on demand |
| DefiLlama free API | TVL, stablecoin supply, chain fees | none (key only for Pro) | generous, no published cap | 15 min |
| Etherscan free key | ERC-20 flows, exchange wallet labels | free key | 5 req/s, 100k/day | 15 min |

**Decision**: M6 is gated on the spec's "free source only" rule (deliver only if a free source exists) — FRED + mempool.space +
DefiLlama + alternative.me satisfy it; Etherscan stays optional because its free key can be
revoked and its labels are heuristic.

**Rationale**: Nothing paid, all have documented or polite-use limits, all fit a 15-minute
cadence. On-chain signals are *context* only; they never size a position.

**Alternatives considered**: Glassnode/Nansen/Santiment (paid); Coin Metrics community
(discontinued coverage of the metrics we need).

---

## R5. Indodax API and venue facts — verified 2026-11-08

Full evidence table with source URLs: [`docs/adr/0001-indodax-venue-facts.md`](../../docs/adr/0001-indodax-venue-facts.md).
Every figure below was read on 2026-11-08 from the [official API documentation PDF](https://indodax.com/downloads/INDODAXCOM-API-DOCUMENTATION.pdf),
the [official `/api/pairs` endpoint](https://indodax.com/api/pairs), the Indodax Help Center, or
DJP/PMK 50/2025. Values the sources do not state are **not** recorded as numbers.

**Verified**:
- **No native stop order and no OCO.** API 2.0's Create Order accepts `LIMIT`/`MARKET` only
  and rejects a stop type with `-1130 … stop_limit_not_available`; its parameter table has no
  stop/trigger price. API 1.0 documents `order_type: limit, market`; its only `stoplimit`
  occurrence is a `cancelOrder` parameter. OCO is explicitly unavailable. A Stop Limit Order
  does exist in the web/app UI, but it is not reachable from the API and its trigger semantics
  are undocumented.
  ⇒ `venue_profile.native_stop_orders = false` is **proven**, so FR-067's client-side stop
  monitor is the primary protective mechanism, not a fallback.
- **Minimum order**: Rp10.000 on all 438 IDR pairs (`trade_min_base_currency`); Rp25.000 to
  trade directly on INDODAX Pro, with Rp10.000–Rp24.999 auto-routed to Lite. The API itself
  accepts Rp10.000.
- **Per-pair service fee**: IDR maker **0.1%** / taker **0.2%** (identical on all 438 pairs);
  USDT maker 0.03% / taker 0.06%. Fields `trade_fee_percent_maker` / `_taker` of `/api/pairs`.
- **Tick size** = `pricescale` (= `price_precision`), per pair: BTCIDR and ETHIDR Rp1.000,
  USDTIDR and XRPIDR Rp1.
- **Quantity step** = `quantity_increment`, per pair: `1e-08` for BTCIDR/ETHIDR (384 pairs
  use `1e-08`, 61 use `1`, 5 use `1e-06`).
- **Cost structure**: all-in fee = service fee + tax + CFX fee, charged on maker *and* taker,
  on both Buy and Sell, on both IDR and USDT markets.
- **Tax**: PPh final **0.21%**, seller side, since 2025-08-01 (PMK 50/2025); PPN on crypto
  transfers is **0%** since the same date.
- **Rate limits**: public API 180 req/min; private API 300 req/min per IP per endpoint (50 for
  capital/fiat); order creation additionally 20 req/s per account per pair, and exceeding it
  triggers a **5-second trading block** on that pair; cancel 30 req/s.
- **Mechanics that affect our design**: self-trade prevention per order (`smp_cancel` =
  MAKER default / TAKER / BOTH), `time_in_force` = GTC or MOC only (so "cancel if unfilled in
  45 s" cannot be expressed and must stay client-side), `client_order_id` ≤ 36 chars
  `[A-Za-z0-9_-]`, and the API ships a **Deadman Switch** endpoint section.

**Unverified — deliberately left as NULL, never guessed**:
- **CFX fee percentage.** Only its existence and effective date (1 October 2026, SEB
  001/SEB2026/CFX-KKI/SPOT/II/2026) are published; the breakdown page is gone and the in-app
  *Biaya Trading* page needs a logged-in account. The earlier figures in this file
  (0.0111% → 0.00555%) had no accessible source and are **withdrawn**.
- **Whether `trade_fee_percent_taker` already includes tax and CFX.** No official statement.
- **Whether a tax component is charged on the buy side.** PMK 50/2025 puts PPh on the seller;
  the "both sides" statement in the Help Center is about the fee *adjustment*, not the tax.

**Consequence for the cost model**: `venue_profile.cfx_fee_pct` is `NULL` in the seed
(`infra/db/seeds/0001_venue_profile_indodax.sql`) and the Risk Engine must refuse to issue a
verdict while it is `NULL`. Closing the gap is a data change from the operator's own
All-in-Fees detail, not a code change.

**Alternatives considered**: hardcode 0.2% and be done — rejected, FR-053 requires venue data
to be configuration and this structure changed twice in two years; take the numbers from a
blog post — rejected, no accessible source states them today.

---

## R6. Round-trip cost model per venue

**What is known and what is missing** — the model is a formula over `venue_profile`, never a
constant:

| Component | Value | Applied to | Status |
| --- | --- | --- | --- |
| Service fee, taker | 0.20% (IDR market) | **entry and exit** — the default for both sides | Verified (`/api/pairs`) |
| Service fee, maker | 0.10% (IDR market) | limit entry only, if it rests and fills | Verified, but **never the default** |
| PPh final | 0.21% | **sell** notional only | Verified (PMK 50/2025) |
| PPN | 0% | — | Verified (PMK 50/2025) |
| CFX fee | **unknown** (`NULL` in the profile) | presumably both sides | Unverified |
| Slippage allowance | 0.10% per side | entry and exit | Planning assumption (R7 measures it) |

Which side pays what is decided by the *exit mechanism*, not by optimism. Stop and close
orders are reduce-only and must fill now, so they execute against resting liquidity: the
**exit leg is always priced at taker**. A limit entry that rests and fills as maker is a
bonus that the cost model may not book in advance, so the default round trip is

```text
F_default = taker_buy + taker_sell + PPh_sell        # = 0.20 + 0.20 + 0.21 = 0.61% (IDR)
```

Maker pricing may only be used when a `RiskVerdict` records that the entry order was
actually filled as maker — never as an assumption at proposal time, because a stop that
triggers before the maker fill would then be scored against a cost the trade never paid.

With round-trip friction `F` = entry fee + exit fee + tax + CFX, the net requirement is

```text
target ≥ 1.5 · (stop + F) + F        # R:R 1.5 measured after costs
```

Using only the **verified** components (taker both sides, tax on the sell) gives `F ≥ 0.61%`,
which is a **floor, not the model** — CFX can only raise it:

| Stop | Target needed at `F = 0.61%` (floor) | + slippage 0.10%/side |
| --- | --- | --- |
| 2% | ≥ 4.53% | ≥ 4.83% |
| 5% | ≥ 9.03% | ≥ 9.33% |

**Decision**: the Risk Engine computes `F` from the active `venue_profile` at verdict time,
stamps `cost_model_version` + `venue_profile_id` into the verdict, and returns a refusal
(`COST_MODEL_INCOMPLETE`) while any cost component is `NULL` in **Live**. In **Paper** and
**Backtest** the missing components are supplied from the separately stored `*_assumed`
columns and the verdict is stamped `cost_model_source = ASSUMED`; a Live verdict may never
read an assumed value. No task may hard-code 0.61%, 0.81%, or any other roll-up: a change of
one component must move every verdict, not require a code edit.

**Open item that blocks Live promotion**: the Lite/Pro boundary. Our 25%-of-equity position
cap on Rp100.000 capital is a Rp25.000 order — exactly the Pro floor — while every order
between Rp10.000 and Rp24.999 is auto-routed to Lite. Nobody has documented whether Lite
exposes the same `order_type`, the same fee percentages, the same `min_notional`, or the same
order rate limit as Pro. Until that is verified, the effective venue for half our trades is
an undocumented market.

**Rationale**: FR-026/FR-058 require net-of-cost R:R. The previous 1.03% round-trip figure in
this file was an assumption chained to an unverifiable CFX number; publishing it as fact
would have sized every trade against a cost the system cannot substantiate.

**Alternatives considered**: assume CFX 0 — rejected, it silently understates cost and is
indistinguishable from a verified zero in the schema; assume both-sides tax (`BOTH_SIDES`) —
rejected as the stored value, because PMK 50/2025 is explicit that the income tax falls on the
seller; it stays an open question for the account probe rather than a silent 0.21% surcharge.

---

## R7. Slippage deviation tolerance for the promotion gate

**Decision**: For every live fill, deviation = (fill price − expected price) / expected
price, where expected price comes from walking the stored order book at proposal time
(`depth` snapshot referenced by the `decision_record`). Gate to leave Co-pilot:

- median |deviation| ≤ **0.25%**
- p95 |deviation| ≤ **0.50%**
- no single fill worse than **1.00%**
- ≥ 20 live fills in the sample, else the gate stays closed

**Rationale**: With Rp10.000–25.000 orders the book at the top level usually covers the
order, so a well-behaved adapter should land inside 0.25%. 0.50% p95 catches a badly sized
order or a depth-walk bug; the 1.00% hard bound catches a stale-book bug that would
otherwise look like bad luck.

**Alternatives considered**: comparing against the last trade price (noise, not
executability); a fixed Rp amount (meaningless across pairs).

---

## R8. Position sizing feasibility at Rp100.000 (the binding constraint)

Given equity E = Rp100.000, risk budget 0.5% ⇒ **Rp500 per trade**, minimum order
Rp10.000, single-position cap 25% ⇒ **Rp25.000**:

size = 500 / SL% ⇒ SL 2% → Rp25.000 (at cap), SL 5% → Rp10.000 (at venue floor).

⇒ **Only stop distances between 2% and 5% are expressible at this capital level.** A 1%
stop would need Rp50.000 (rejected by FR-024), and a 6% stop would need Rp8.333 (rejected
by FR-051 / venue minimum). Combined with R6, the 2% stop sizes at the cap and the required
target follows from the net-cost formula (R6), not from a fixed percentage here
up to ≥ 8.8% (SL 5%) — i.e. only clean swing setups on liquid IDR pairs.

Consequences the plan must accept explicitly:
1. Expected trade frequency is low: order of 1–4 trades per month per active pair. With
   20 watched pairs, ~10–30 trades/month is plausible but not guaranteed.
2. The promotion gate "≥ 10 closed trades in 30 days" (FR-059) may not be reachable in a
   single 30-day window. The correct response is to **extend the observation window**
   (operator decision, recorded), never to loosen risk per trade or the 25% cap.
3. Rounding to tick/step size means realised risk is usually *below* 0.5%; the Risk Engine
   MUST round size **down** and report the realised risk in the verdict.
4. The system's value at this capital is evidence generation, not profit. Compute and show
   the minimum viable capital in the dashboard (a config-only change later).

**Alternatives considered**: allowing >25% single position (rejected: violates FR-024);
allowing >0.5% risk (rejected: constitution II + FR-023/FR-025); futures/leverage (explicitly
out of scope, spot-only).

---

## R9. Venue adapter: native vs ccxt

**Decision**: Native Indodax adapter (REST + WS) implementing our own `VenuePort`.
ccxt is allowed only for *later* venues, behind the same port, and never as the Indodax
implementation.

**Rationale**: The port must expose things ccxt does not model uniformly: all-in fee
breakdown incl. CFX, Indonesian PPh, minimum notional per side, self-trade prevention,
order-history pagination quirks, and the OJK licence flag. ccxt would still require all of
this as side-channel config, so it buys nothing for the launch venue while adding a large
dependency (Principle X).

**Alternatives considered**: ccxt-only (fast to start, leaks venue semantics into the
Risk Engine through ambiguous fee fields); vendor SDKs (none that are maintained).

---

## R10. Agent orchestration: custom state machine vs LangGraph

**Decision**: A hand-written async state machine in `services/agents`
(`IDLE → GATHER → ANALYSE → PROPOSE → REVIEW → EMIT → COOLDOWN`) with explicit
transitions, per-step timeouts, and every step persisted in `agent_run`. No LangGraph.

**Rationale**: Principle VII requires the full rendered prompt and every intermediate
artifact; a framework's hidden checkpointing makes that harder, not easier. Deterministic
tests with stubbed model responses (Principle IX) are trivial against explicit states and
painful against a graph runtime. YAGNI (Principle X) also applies: the graph here is
six states, not a DAG engine.

**Alternatives considered**: LangGraph (good checkpointing, but a runtime dependency whose
state lives outside our audit chain); plain function calls without a state machine (loses
resumability after a crash mid-cycle).

---

## R11. Storage sizing and retention on a small VPS

**Decision**:
- `market_bars_1m` hypertable: 20 pairs × 1440 = 28.8k rows/day, ~0.3 KB/row ⇒ ~9 MB/day
  raw, ~10× compression ⇒ 90-day retention ≈ **~100 MB**.
- No raw tick persistence. Last 500 trades per pair live in Redis (capped list) for
  slippage estimation; the order-book snapshot referenced by a decision is stored as JSONB
  on the `decision_record` (a few KB, 96/day ⇒ ~3 MB/day, 90 days ⇒ ~270 MB).
- `news_item` + `news_embedding`: ~500 items/day, 2 KB text + 384-dim vector (1.5 KB)
  ⇒ ~2 MB/day ⇒ 90 days ≈ 180 MB.
- `decision_record`: ~30 KB each after prompt + output + verdict ⇒ ~3 MB/day ⇒ 270 MB.
- Budget: `pg_dump` nightly compressed to object storage / second disk, 7 daily + 4 weekly.
- Total live DB target **< 1.5 GB**; disk alarm at 70%.

**Rationale**: The 40 GB VPS disk must also hold images and logs. Continuous aggregates
(1m → 5m → 1h) give the backtester multi-timeframe data without storing ticks.

**Alternatives considered**: storing every trade tick (≈1.7M rows/day at 1 s ⇒ ~500 MB/day,
impossible on this VPS); ClickHouse (a second engine, Principle X).

---

## R12. Observability within the budget

**Decision**: Prometheus (30 s scrape, 15-day retention, local TSDB) + Grafana + alert
rules evaluated by Prometheus; logs via structlog JSON to journald with 14-day rotation;
**Loki deferred** to a later milestone if disk and RAM allow. One Grafana dashboard set:
system health, data freshness, decision cycle, risk verdicts, orders/fills, LLM spend.

**Rationale**: Metrics are what the promotion gates and SLAs need; Loki's 300–500 MB RSS
plus indexing is the one component that does not fit 4 GB alongside Postgres, Redis, eight
Python services, and Next.js. Alerting does not depend on Loki (Prometheus → Alertmanager
or, cheaper, a small alert-relay service that posts to Telegram).

**Alternatives considered**: Grafana Cloud free tier (free but egress + account
dependency); ELK (far too heavy); no logs beyond files (fails the correlation-ID requirement of constitution VII —
journald + `journalctl -o json` still gives correlation IDs, so this is covered).

---

## R13. VPS specification and cost

**Decision** (minimum → recommended):

| Tier | Spec | Cost/mo | Fits |
| --- | --- | --- | --- |
| Minimum | 2 vCPU, 4 GB RAM, 40 GB NVMe, static IPv4 | ≈ Rp65.000–75.000 (Hetzner CX22 class) | all 8 services, 2 vCPU is enough at 15-min cadence |
| Recommended | 4 vCPU, 8 GB RAM, 80 GB NVMe, static IPv4 | ≈ Rp95.000–110.000 (Contabo VPS S class) | adds Loki + comfortable page cache |

**Rationale**: The total budget is Rp250.000 and LLM worst case is Rp150.000, leaving
≈ Rp100.000 for infra. A Jakarta-region VPS (IDCloudHost, Biznet Gio) costs Rp150.000+ for
less RAM; at a 15-minute decision cadence the 150–300 ms RTT to Indodax is irrelevant, so
geography is not worth the money. Static IPv4 is mandatory (exchange IP whitelist).

**Alternatives considered**: managed Postgres/Redis (Rp300.000+ alone); running on a home
server (no static IP, no UPS, violates the availability assumptions).

---

## R14. Secrets and operator authentication

**Decision**: sops+age. Ciphertext in `infra/secrets/*.enc.yaml`, committed; age private
key only on the VPS at `/etc/atlas/age.key` (root:root 0600). A systemd unit decrypts to
`/run/atlas/*.env` (tmpfs) before Compose starts; containers get env vars, never files.
Rotation = re-encrypt + restart, documented in a runbook.

Operator auth: Tailscale-only ingress (no public ports except the Telegram webhook, which
is authenticated by bot token + allowlist + TOTP). Dashboard session = HttpOnly, Secure,
SameSite=Strict cookie; TOTP (pyotp) mandatory; passkey (webauthn) optional; login
rate-limited and audited; kill switch reachable without the dashboard.

**Rationale**: No secret ever enters git, images, logs, prompts, or LLM requests
(Principle VIII). Tailscale removes the entire public attack surface for the UI without
paying for a WAF.

**Alternatives considered**: Vault (heavy for one operator); Docker secrets (still needs a
bootstrap secret, and we need per-mode files); plain `.env` on disk (no rotation story, no
audit).

---

## R15. Scheduling and leader election

**Decision**: Each service runs its own asyncio loop; the decision cadence lives in
`services/agents` guarded by a Redis leader lock (`SET atlas:leader:cycle <id> NX PX 900000`,
renewed every 60 s). Missed cycles are recorded as `cycle_skipped` with a reason. No
APScheduler, no Celery, no separate scheduler service.

**Rationale**: One lock, one owner, auditable skips. A restarted container cannot double-run
a cycle, which would double LLM spend and could double-submit (the `client_order_id` still
protects orders, but the cheaper fix is to never run twice).

**Alternatives considered**: systemd timers per cycle (no visibility into overlap);
Celery beat (broker + worker model for a 96-event/day workload).

---

## R16. Backtester fidelity

**Decision**: Event-driven replay over stored `market_bars_1m` + historical news, importing
the **same** `risk-engine` package and a `SimulatedVenue` that consumes the same
`venue_profile` (fees, PPh, CFX, min notional, tick size, step size, depth model). Slippage
model = linear walk of the historical book snapshot when available, else
`spread/2 + k·size/ADV`. Every run stamps `cost_model_version`, code version, and config
hash.

**Rationale**: FR-049 and Principle VII demand that a backtest and a paper/live decision
diverge only through data, never through a re-implemented risk rule. Sharing the package is
the only way to guarantee that over time.

**Alternatives considered**: a vectorised pandas backtester (fast, but a second
implementation of sizing and costs — the exact drift that produces false promotion
evidence); third-party backtest frameworks (fees/tax models do not match Indonesian rules).

---

## R17. The runtime database identity is part of the safety argument

Append-only tables in this system are defended by triggers, not by grants: `audit_record`,
`mode_state` and the rest refuse `UPDATE`, `DELETE` and `TRUNCATE` from every caller. A
trigger, however, can be switched off by the table owner — `ALTER TABLE ... DISABLE TRIGGER
USER` — and by a superuser. Grant-level defence has the same shape from the other side: the
`REVOKE` in migration `0004_audit` is only meaningful for a role that is not the owner,
because ownership implies every privilege.

So the guarantees survive only if the application connects as a non-owner identity whose
rights are exactly `SELECT` + `INSERT` (`USAGE, SELECT` on the sequences). No grant can
express "may not alter": Postgres has no `ALTER` privilege to withhold — ownership is the
thing that unlocks DDL, which is why the role must not own anything.

Pre-Live requirement (blocks promotion, checked in `T097`):

* the API gateway and every worker connect as a dedicated role, e.g. `atlas_app_runtime`;
* that role owns no table, sequence or schema, and holds no `TRIGGER`, `REFERENCES` or DDL
  grant;
* migrations run as a separate owner role, never as the runtime role;
* `tests/integration/test_db_role_privileges.py` proves both halves: the runtime role can
  append, cannot disable or drop the trigger, and cannot rewrite history; and the owner
  connection *can* disable the trigger, which is why it must not be the runtime identity.

Until this is in place the system is one `psql` session away from a rewritten audit log.

## Open items that remain, and where they are resolved

| Item | Blocking | Resolved by |
| --- | --- | --- |
| Indodax CFX percentage and whether `trade_fee_percent_taker` includes tax + CFX | M3 (cost model) | **Open.** Docs do not state either (`ADR 0001` facts 6–7). Close with the operator's All-in-Fees detail, then a new `venue_profile` version. Until then `cfx_fee_pct IS NULL` and the engine refuses (`COST_MODEL_INCOMPLETE`) |
| Whether a tax component is charged on the buy side | M3 (cost model) | **Open.** PMK 50/2025 puts PPh on the seller; stored `SELL_ONLY` (`ADR 0001` facts 8, 10). One real buy fill settles it |
| Everything else about Indodax (stop/OCO, min order, tick, step, rate limits, fees) | — | **Resolved 2026-11-08**: `docs/adr/0001-indodax-venue-facts.md` + `infra/db/seeds/0001_venue_profile_indodax.sql` |
| Concrete cheap/strong model IDs and prices | M1 (first LLM call) | config + `llm_price` table refreshed at M1; ledger makes drift visible |
| Second OJK venue for the adapter conformance suite | M6 | operator choice at M6; port conformance suite already in `tests/contract` |
| Whether Indodax exposes native stop / bracket orders (OCO) | M3 (stop placement path) | **Resolved 2026-11-08**: not exposed by the API, no OCO; UI Stop Limit is undocumented and unreachable ⇒ `native_stop_orders = false`, FR-067's client-side stop monitor is the primary mechanism (`ADR 0001` facts 1–3) |
