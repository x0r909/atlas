# Implementation Plan: ATLAS — Autonomous Crypto Trading System

**Branch**: `001-autonomous-crypto-trader` | **Date**: 2026-11-08 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-autonomous-crypto-trader/spec.md`

## Summary

ATLAS is a single-operator, spot-only, swing-trading system (4 h–3 d horizon, 15-min
monitoring cycle) that runs on one small VPS inside a Rp250.000/month budget. The design
keeps a hard deterministic boundary: LLM agents may only emit a schema-validated
`TradeProposal`, which the deterministic Risk Engine either signs or rejects; only the
Execution Engine holds trade-only exchange credentials and only submits signed orders.

Implementation: a Python 3.12 asyncio monorepo of nine services wired through Redis
Streams, with PostgreSQL + TimescaleDB + pgvector as the system of record, a Next.js
operator terminal, Docker/Podman Compose on a 2 vCPU/4 GB VPS (4 vCPU/8 GB recommended),
sops+age for secrets, Telegram as the out-of-band kill switch. Paper is the default mode;
Co-pilot (the constitution's LIVE-small-capital stage) is capped at Rp30.000 of the
Rp100.000 total capital. Milestones M0–M8 in the spec are the delivery order, and every
milestone ends behind a promotion gate that only the operator can open.

## Technical Context

**Language/Version**: Python 3.12 (asyncio, `TaskGroup`) for all backend services; TypeScript 5.x on Node 22 LTS for `apps/terminal-web` (Next.js 15, App Router).

**Primary Dependencies**:
- Backend: FastAPI + uvicorn, pydantic v2 (+ `pydantic-settings`), httpx, websockets, redis-py (`asyncio`), SQLAlchemy 2.0 async + asyncpg, Alembic, structlog, prometheus-client, feedparser, `pyotp`, `webauthn`, `sops`/`age` (infra, decrypt-at-deploy only).
- Frontend: Next.js 15, React 19, Tailwind CSS 4, `lightweight-charts`, native `fetch` + `WebSocket` (no data-fetch or state library — a chart, a table and a socket do not justify one).
- Deliberately not used: ccxt for the Indodax adapter (native REST/WS instead — R9), LangGraph/celery/APScheduler (R10, R15), paid market-data or news vendors (FR-052).

**Storage**: PostgreSQL 16 with TimescaleDB 2.x (1-minute hypertables, compression, 90-day retention) and pgvector 0.7 (news embeddings, 384-dim, HNSW); Redis 7 for Streams (event bus), rate-limit counters, and the scheduler leader lock. Raw tick data is not persisted (R11).

**Testing**: pytest + pytest-asyncio (unit/integration), respx (HTTP/Venue stubs), `time-machine` (clock control), Hypothesis for the Risk Engine property tests, Vitest + Playwright (terminal smoke). CI gates: ruff, mypy (strict on `packages/schemas`, `services/risk-engine`, `services/execution`), pip-audit, gitleaks. Chaos suite per constitution IX.

**Target Platform**: Linux x86_64 VPS (Ubuntu 24.04 LTS), Docker/Podman Compose; external access only through Tailscale/WireGuard.

**Project Type**: multi-service monorepo — 9 backend services, 1 web app, 2 shared packages, `infra/`.

**Performance Goals**:
- Decision cycle: complete within 60 s of the 15-min trigger (p95), hard cap 180 s.
- Risk verdict: p95 ≤ 50 ms, deterministic, no I/O except a pre-loaded portfolio snapshot.
- Order acknowledgement: p95 ≤ 1.5 s end-to-end (Indodax REST RTT 150–300 ms from a Jakarta-region VPS).
- Verdict lifetime: an entry verdict is usable for exactly 60 s from issue and exactly once (FR-062); a signed order past `expires_at` or already consumed is refused locally, before any venue call, in ≤ 5 ms.
- Stop monitor: protective orders checked every 10 s; a heartbeat older than 30 s puts the system in HALTED within 5 s and triggers one close attempt per open position (FR-067, SC-022).
- Terminal live updates: ≤ 1 s from exchange event to browser; REST p95 ≤ 300 ms.
- Ingestion: ≥ 5 msg/s sustained, 200 msg/s burst, zero unbounded queues.
- Reconciliation: full venue+portfolio reconcile every 5 min, ≤ 5 s duration.

**Constraints**:
- Cash budget Rp250.000/month total, LLM ≤ Rp5.000/day ⇒ infra share ≤ Rp100.000/month ⇒ one VPS, no managed services, no log shipping.
- Capital Rp100.000 total; Co-pilot cap Rp30.000; Indodax minimum order Rp10.000 ⇒ the strategy can only express stop distances in a narrow band (R8) — this is a first-class design constraint, not a footnote.
- Single VPS = no HA. The system MUST fail safe (halt), never fail open; all state needed for a safe halt lives on local disk + Postgres with daily off-box backup.
- Disk ≤ 80 GB; RAM ≤ 4 GB on the minimum spec, ≤ 8 GB recommended.
- LLM calls: ≤ 6k input + 1k output tokens per monitored cycle; strong model only for Strategist on candidate setups.
- Exchange keys: trade-only, withdrawal disabled, IP whitelisted (static VPS IP); separate keys per mode; held by `services/execution` only — no other process receives them (FR-064).
- Time: `chrony` mandatory; every freshness check uses the synchronized clock. Daily-loss boundary is 00:00 WIB; the drawdown high-water mark is seeded per Live stage (FR-066).

**Scale/Scope**: 1 operator, 1 exchange account per venue, ≤ 20 tracked IDR pairs at launch, ≤ 5 concurrent positions, 96 decision cycles/day, ~28.8k 1-minute bars/day across 20 pairs, ≤ 5 news items/cycle after dedupe, 9 deployable services, ~35–45k LOC target for M0–M5.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v2.0.0. Mode mapping declared explicitly: spec's **Paper** = constitution PAPER; spec's **Co-pilot** = constitution **LIVE (small capital)**; spec's **Autonomous** = constitution AUTONOMOUS; **Backtest** and **Halted** are non-trading states and do not constitute a rollout stage.

| Principle | Gate for this plan | Status |
| --- | --- | --- |
| I. Decision–Execution Separation | LLM agents emit `TradeProposal` only; pydantic v2 schema validation at the agent boundary; execution accepts only `SignedOrder` produced by the Risk Engine, and only when the envelope's producer is on the stream allowlist; only `services/execution` mutates orders (place/cancel/modify) and only it holds venue credentials — no other process has the keys or a credentialed path to an order endpoint (FR-064, FR-071); the LLM process has no exchange credentials and no HTTP client capable of calling venue trade endpoints (separate network policy + credential-scoped env); the operator gateway forwards close and stop to `execution` as a `ManagementAction`/`ProtectiveStopRequest` and never reaches a venue itself; an entry order is refused with `MODE_NOT_TRADABLE` in Paper (live-enabled venue) or Halted while reduce-only orders proceed (FR-001, FR-063). | PASS |
| II. Deterministic Risk Gate | `services/risk-engine` is pure: snapshot in → `RiskVerdict` out, no I/O, no LLM, no clock reads except the injected timestamp. Every FR-020…FR-029 rule implemented as a named rule with a fixed rule id and a property test. Order signature = HMAC over the immutable order payload + verdict id, bound to proposal/venue/symbol/side/size, single use, expiring 60 s after issue; `risk_verdict` stores signature, `expires_at`, and `consumed_at`, and execution refuses consumed/expired/altered verdicts and records the refusal (FR-062). | PASS |
| III. Fail-Safe Defaults | Every failure path (stale data, LLM error, mismatch, breaker, unreadable mode) resolves to HALTED/no-new-positions; global `MONITORING` and kill switch keep working with agents down; open positions keep managed stops. Watchdog service with independent liveness checks, plus the stop monitor as its own process whose missing 30 s heartbeat forces HALTED and one close attempt per position (FR-067). Reduce-only orders are never blocked by halt, breaker, or spend cap (FR-063). | PASS |
| IV. Staged Rollout & PAPER default | Compose ships `ATLAS_MODE=PAPER`; invalid/unset ⇒ PAPER; LIVE requires an authenticated operator command recorded in the audit log; Co-pilot hard cap Rp30.000 enforced by both Risk Engine and Execution Engine; promotion only via measured gates (FR-059) with operator confirmation. | PASS |
| V. Operator Control | Kill switch via dashboard and Telegram (allowlist + TOTP/PIN), no LLM in the path; global limits configurable; limit breach auto-halts and only the operator can lift. | PASS |
| VI. External Text Untrusted | All news/macro/on-chain text wrapped as delimited data blocks with an injection-pattern pre-screen; content can never alter mode, limits, config, or tools; decisions still pass schema + Risk Engine. | PASS |
| VII. Auditable & Reproducible | `decision_record` append-only with hash chain, full prompt, model id/version, params, raw output, verdict, orders, fills, code version, config hash; replay test re-runs the Risk Engine from the stored snapshot and asserts identical verdict + order requests. | PASS |
| VIII. Credential Hygiene | sops+age encrypted files decrypted to tmpfs at deploy; per-mode keys; startup permission probe (withdrawals enabled or whitelist inactive ⇒ refuse LIVE); secret redaction filter in structlog; gitleaks in CI. | PASS |
| IX. Test-First & Chaos | Every task in `tasks.md` starts from a failing test; chaos suite covers stale data, venue down/timeout, LLM timeout/invalid output, rejected orders, partial fills, SL placement failure, reconciliation mismatch, kill-switch drill; AI tests use recorded/stubbed responses only. | PASS |
| X. Simplicity (YAGNI) | One VPS, one datastore pair, no message broker beyond Redis Streams, no workflow engine, no ORM beyond SQLAlchemy Core-ish usage, frontend without a state library. Two justifications recorded in Complexity Tracking; everything deferred (pgvector tables, Loki, second venue) is listed there too. | PASS |
| Trading constraints | Idempotent order submission via `client_order_id`; startup + 5-min reconciliation; NTP; per-cycle token/step caps plus daily/monthly budget caps; dashboard WCAG 2.1 AA (keyboard, focus, contrast) with the kill switch always visible. | PASS |

No violations require a waiver of Principles I–IX. Principle X deviations are recorded in
Complexity Tracking with the rejected simpler alternative.

**Post-design re-check (after Phase 1)**: PASS — see [data-model.md](./data-model.md)
(hash-chained `decision_record`, venue-scoped idempotency), [contracts/](./contracts/)
(risk engine is the only producer of a single-use, 60 s `SignedOrder`; venue port has no withdraw method; `stop-monitor` owns protection without holding keys),
and [quickstart.md](./quickstart.md) (chaos + promotion-gate scenarios are runnable).

## Project Structure

### Documentation (this feature)

```text
specs/001-autonomous-crypto-trader/
├── plan.md              # This file
├── research.md          # Phase 0 output — 16 resolved decisions
├── data-model.md        # Phase 1 output — entities, tables, states, retention
├── quickstart.md        # Phase 1 output — runnable validation scenarios
├── contracts/
│   ├── venue-adapter.md # Uniform venue port (market data, order, balance, fees, limits)
│   ├── decision-risk.md # TradeProposal / RiskVerdict / SignedOrder schemas + rule ids
│   ├── stop-monitor.md  # Protective-order path: heartbeat, watchdog, resize, escalation
│   ├── event-bus.md     # Redis Streams topics, envelope, consumer groups
│   ├── api-gateway.md   # Operator REST + WebSocket contract
│   └── telegram-control.md # Remote kill switch contract
├── checklists/
│   ├── requirements.md            # Spec quality gate
│   └── risk-execution-security.md # Requirements-quality review (41 items)
└── tasks.md             # Phase 2 output (/speckit.tasks — NOT created here)
```

### Source Code (repository root)

```text
services/
├── ingest-market/        # Indodax REST+WS market data → bars, latest quote, freshness
├── ingest-news/          # RSS/macro/on-chain pollers, dedupe, embed, injection screen
├── feature-engine/       # Deterministic indicators/features from bars + news signals
├── agents/               # LLM orchestration: Analyst, Strategist, Critic (state machine)
├── risk-engine/          # Pure sizing + rule gate → SignedOrder | RejectedOrder
├── execution/            # Only holder of exchange keys; sole order writer: submit, cancel, modify, reconcile, SL
├── stop-monitor/         # Separate process: protective-order heartbeat, re-place, escalate (no keys)
├── backtester/           # Event replay reusing risk-engine + execution simulator
└── api-gateway/          # Operator REST/WS, auth, mode control, Telegram bot bridge

apps/
└── terminal-web/         # Next.js 15 operator terminal (charts, positions, audit, kill switch)

packages/
├── schemas/              # pydantic v2 models shared by all services (single source of truth)
└── common/               # config, logging, clock, redis stream helpers, crypto/hash-chain, money

infra/
├── compose/              # docker-compose.yml + per-service Dockerfiles
├── db/                   # Alembic migrations, Timescale policies, seed data
├── observability/        # Prometheus rules, Grafana dashboards (Loki deferred)
├── secrets/              # sops+age encrypted env files (never plaintext)
└── vps/                  # bootstrap, chrony, tailscale, backup, systemd units

docs/
├── adr/                  # Architecture decision records (one per research.md decision)
├── runbooks/             # halt, promote, reconcile-mismatch, kill-switch drill
└── operator/             # promotion checklist, incident definitions

tests/
├── contract/             # venue port conformance suite (replayed fixtures)
├── integration/          # paper loop end-to-end, Redis Streams, DB migrations
├── chaos/                # stale data, venue down, LLM failures, partial fills
├── unit/                 # risk rules, sizing, money math, hash chain
└── fixtures/             # recorded venue payloads + stubbed model responses
```

**Structure Decision**: Monorepo with one deployable per service directory and a single
shared `packages/schemas` that both Python services and the TypeScript app consume via
generated types. `risk-engine` and `execution` are separate deployables (not libraries
inside `agents`) so that the credential-free and credential-holding processes have
different environment and network policies — this is the structural expression of
Principle I. The backtester imports `risk-engine` code directly (same package) so that
FR-049 and Principle VII replay use the identical rule implementation, never a copy.
`stop-monitor` is a ninth deployable precisely because it must be able to observe and
report the failure of `execution` itself: it holds no exchange credentials, proposes
protective orders to `execution` (which refuses them only on signature/lifetime grounds,
never throttles them — FR-063), and its own heartbeat is what forces HALTED (FR-067).
The order state machine (`PENDING_NEW → OPEN → PARTIALLY_FILLED → FILLED`, plus
`CANCELED`, `REJECTED`, `EXPIRED`, and the non-terminal `UNKNOWN` that freezes the venue
until resolved — FR-069) is defined in `contracts/decision-risk.md` and mirrored by the
`order` table in `data-model.md`.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
| --- | --- | --- |
| 9 separately deployed services (vs. one process with modules) | Principle I needs process-level credential separation between agents and execution, and Principle III needs the kill switch + risk core to survive an agent crash or memory blowup. Each service also has a different restart/failure profile, and the stop monitor must outlive the execution process it reports on (FR-067). | A single process with modules would put exchange keys in the same memory space as LLM orchestration and let an agent OOM take down the kill switch — a direct Principle I/III violation. Managing stops from a thread inside `execution` was rejected because that monitor dies with the process it is supposed to police, so its absence could never be detected as a heartbeat loss. |
| PostgreSQL + TimescaleDB + Redis (vs. SQLite + in-process queue) | TimescaleDB gives compression + retention policies that keep 90 days of 1-minute bars inside 40 GB; Redis Streams gives at-least-once consumer groups and a leader lock with 3 lines of config instead of a custom queue. | SQLite is single-writer and cannot ingest market data while the API serves queries; an in-process queue loses events on restart, breaking auditability and reconvergence after a crash. |
| Full observability stack present from M0 (Prometheus + Grafana), Loki deferred | Constitution requires measurable performance and incident evidence for promotion; metrics are cheap (~200 MB). Log shipping is the expensive part, so logs stay in journald with rotation until the budget allows Loki. | No metrics means promotion gates and SLAs cannot be evidenced; shipping Loki at M0 does not fit the 4 GB / Rp100.000 infra envelope. |
| pgvector extension installed at M0, embedding tables only from M6 | Installing the extension during M0 avoids a Postgres restart/migration during a live stage; unused tables are not created, so no speculative schema exists. | Deferring the extension entirely would force a database maintenance window before M6 (news retrieval), which conflicts with the "no risky DB changes while live" runbook. |
