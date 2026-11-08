# Contract: Operator API Gateway (REST + WebSocket)

**Owner**: `services/api-gateway` + `apps/terminal-web`
**Audience**: the single operator, over Tailscale only

Base URL: `https://atlas.<tailnet>.ts.net/api` — no public listener. All responses JSON;
timestamps are epoch ms (UTC) unless stated. Errors use a single shape:

```json
{ "error": { "code": "MODE_TRANSITION_INVALID", "message": "human readable", "details": {} } }
```

| HTTP | code examples |
| --- | --- |
| 400 | `VALIDATION_ERROR`, `MODE_TRANSITION_INVALID`, `LIMIT_OUT_OF_RANGE` |
| 401 | `UNAUTHENTICATED`, `TOTP_REQUIRED`, `TOTP_INVALID` |
| 403 | `FORBIDDEN`, `CONFIRMATION_REQUIRED` |
| 409 | `CONFLICT_RECONCILIATION_MISMATCH`, `CREW_ALREADY_OPEN`, `ALREADY_HALTED`, `MODE_NOT_TRADABLE` |
| 423 | `LLM_BUDGET_EXHAUSTED` (read-only state, not a crash) |
| 429 | `RATE_LIMITED` |
| 503 | `VENUE_UNAVAILABLE`, `DATA_STALE`, `HALTED` |

## Authentication and session

- `POST /auth/login` `{username, password}` → session cookie (HttpOnly, Secure,
  SameSite=Strict, 8 h idle) **and** `{"totp_required": true}`.
- `POST /auth/totp` `{code}` → session promoted. 5 failures ⇒ 15-min lockout + audit event.
- `POST /auth/passkey/begin` / `/auth/passkey/finish` — optional second factor.
- `POST /auth/logout`, `GET /auth/session`.
- Every mutating endpoint is audited (see Confirmation strength for which proof each one needs).

Session invariants (each one is an integration test in `tests/integration/test_auth_flow.py`):

- the session cookie is `HttpOnly`, `Secure`, `SameSite=Strict`, and a session idle for 8 h is
  refused on every route, `GET /auth/session` included;
- a TOTP code is valid once: replaying the previous code for the same time step fails;
- a passkey challenge is single-use: a replayed assertion fails;
- `POST /auth/logout` revokes the session server-side, so the old cookie no longer authenticates
  even when replayed from another client;
- 5 failed logins lock the account for 15 min; the lockout is recorded, not just returned;
- every login attempt (success, failure, lockout) and every operator action writes exactly one
  `audit_record` with actor, timestamp, and source address.

## Read endpoints

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/health` | `{services: [{name, status, last_heartbeat_ms}], data: {…}, mode}` |
| GET | `/mode` | current mode + history (last 20) |
| GET | `/portfolio` | equity, cash, exposure (total/per asset), drawdown, daily PnL, per venue |
| GET | `/positions?status=open` | positions with stop, target, time_stop_at, unrealised PnL |
| GET | `/orders?status=&since=` | orders, fills, fees, tax, reject reasons |
| GET | `/market/pairs` | tracked pairs + last price, spread, freshness (`age_ms`) |
| GET | `/market/bars?pair=&tf=&from=&to=` | OHLCV (max 5 000 bars per request) |
| GET | `/news?since=&pair=` | items with source, flags, cluster id (never the raw prompt) |
| GET | `/decisions?from=&to=&outcome=` | cycles with verdict summary |
| GET | `/decisions/{id}` | full audit bundle: prompts, raw output, verdict rules, orders |
| GET | `/risk/config` | active `risk_config` + version history |
| GET | `/audit?from=&to=&type=` | hash-chained records + `chain_ok` flag |
| GET | `/incidents?state=open` | incidents, severity, auto action |
| GET | `/spend` | LLM spend today/month vs caps, throttle stage, per-model breakdown |
| GET | `/promotion/evaluate?to=CO_PILOT` | dry-run gate evaluation with per-criterion pass/fail |

`/decisions/{id}` supports `?format=md` to download the replay bundle for offline analysis.
Raw prompts are returned only to an authenticated operator, never to the model or to logs.

## Write endpoints (all audited)

### Confirmation strength

Every mutating write is audited, but not every write demands the same proof. `X-Atlas-Confirm`
is a single-use token (60 s TTL) obtained from `POST /confirm` `{action, params_hash}`; it is
bound to one action and one parameter hash, so replaying it for a different body fails.

| Endpoint | Proof required | Why |
| --- | --- | --- |
| `POST /mode` | session + `X-Atlas-Confirm` | changes what the system may do next |
| `PUT /risk/config` | session + `X-Atlas-Confirm` | changes the limits that protect capital |
| `POST /promotion/confirm` | session + step-up re-auth + `X-Atlas-Confirm` | the human promotion act (constitution IV) |
| `POST /halt/lift` | session + `X-Atlas-Confirm` | re-opens entries after a limit halt |
| `POST /positions/{id}/stop` | session only | tightens protection only; reduce-only, so no second step (FR-063, FR-071) |
| `POST /killswitch` | session only | emergency stop — a second step is a new failure mode |
| `POST /positions/{id}/close` | session only | reduces exposure (FR-063, FR-071) |

A missing, expired, reused, or wrong-`params_hash` token is refused with
`403 CONFIRM_REQUIRED` on the four guarded endpoints. It is **never** required for
`POST /killswitch`, `POST /positions/{id}/close`, or `POST /positions/{id}/stop`: all three
must work with nothing more than an authenticated session, and a lockout of the dashboard
account (5 failed logins ⇒ 15 min) neither invalidates an existing session nor touches the
Telegram control path in `contracts/telegram-control.md`, which authenticates on its own
allowlist + TOTP. Tighten-only remains the guard for a stop, not a token: a stop moved away
from the entry is still refused with `409 STOP_NOT_TIGHTENING`.

| Method | Path | Body | Notes |
| --- | --- | --- | --- |
| POST | `/killswitch` | `{scope: "NEW_ENTRIES"\|"ALL", close_positions: bool, reason}` | idempotent; returns within 1 s; works with agents down |
| POST | `/mode` | `{to: "PAPER"\|"CO_PILOT"\|"AUTONOMOUS", evidence_id?, reason}` | promotion requires a passing `promotion_evaluation` id + operator confirm; demotion always allowed |
| PUT | `/risk/config` | full new config | validated ranges; bumps version; never mutates history |
| POST | `/positions/{id}/close` | `{reason}` | operator override; still goes through Execution (never direct venue call from UI) |
| POST | `/positions/{id}/stop` | `{new_stop_price}` | tighten-only; session auth only, no confirm token |
| POST | `/promotion/evaluate` | `{to}` | records a `promotion_evaluation` row |
| POST | `/promotion/confirm` | `{evaluation_id}` | the explicit operator promotion act (constitution IV) |
| POST | `/halt/lift` | `{reason}` | only the operator may lift a limit-triggered halt |

Rules: `POST /mode` to `AUTONOMOUS` is refused unless the last evaluation passed, is < 24 h
old, and has `operator_confirmed_at` set — which only `POST /promotion/confirm` can write.
`POST /killswitch` never depends on LLM, agents,
or the database being writable beyond the audit insert — it writes to Redis first, then
persists, then notifies.

### Which errors may refuse an exit (FR-063, FR-071)

`503 HALTED`, `503 DATA_STALE`, `503 VENUE_UNAVAILABLE`, `409 MODE_NOT_TRADABLE`, and
`423 LLM_BUDGET_EXHAUSTED` apply **only to entry-producing requests** — mode promotion,
risk-config writes, and any endpoint that would open or increase a position. They never
refuse `POST /positions/{id}/close` or `POST /positions/{id}/stop`: those two are forwarded
to Execution and processed while the system is Halted, while a circuit breaker is active,
while the spend cap is exhausted, and while an `UNKNOWN` order freezes the venue. A close or
stop that fails does so for its own reason (venue rejection, unknown position), never because
of a system state. A missing or invalid `X-Atlas-Confirm` token likewise never refuses an exit:
the exit endpoints (`close`, `stop`, and `killswitch`) require only an authenticated session.

### Operator actions on a position (FR-063, FR-071, SC-021, SC-027)

The gateway holds no venue credentials and never calls a venue. Both writes below are
forwarded to Execution as a `ManagementAction` / `ProtectiveStopRequest` and are therefore
subject to the single-writer rule:

- `POST /positions/{id}/close` → `ManagementAction(type=CLOSE)`; the position is closed in
  full (no partial close in v1). The response is the venue's answer, never an optimistic
  acknowledgement, and one `audit_record` is written before success is returned.
- `POST /positions/{id}/stop` → `ProtectiveStopRequest` with a monotonic `generation`.
  **Tighten-only**: a new stop that equals or moves away from the entry is refused with
  `409 STOP_NOT_TIGHTENING` and produces no venue request. Under `HALTED` a tightening stop
  is still accepted (FR-063).

### Promotion confirmation (FR-003, SC-018)

`POST /promotion/confirm` is the only path that can record the human promotion act. It
requires a step-up re-authentication (TOTP or passkey) completed within the preceding 5
minutes, is single-use per `evaluation_id`, and writes an `audit_record` containing the
operator identity, the timestamp, the evaluation id, and the evaluation's criterion results
**before** returning success. An evaluation older than 24 h, already used, or with any
failing criterion is refused. Promotion is void without this record — `POST /mode` cannot
substitute for it.

## WebSocket `/ws`

Client sends `{"op":"subscribe","topics":["market","positions","orders","cycles","health","incidents"]}`.
Server pushes the same envelope shape as the event bus (trimmed payloads) plus a
`{"type":"heartbeat","ts":…}` every 15 s. On reconnect the client calls the REST endpoints
to resync; the socket never claims to be a complete history.

Message types: `market.tick`, `market.bar`, `position.update`, `order.update`,
`cycle.started`, `cycle.finished`, `verdict`, `incident`, `mode.changed`, `spend.update`,
`health.update`.

Guarantees: at-most-once per socket, ordering per topic, ≤ 1 s from exchange event to
browser on a local network. If the socket cannot keep up (> 500 queued messages), the server
drops `market.tick` first (never `position`, `order`, `incident`, or `mode`).

## Non-negotiables from the constitution

- The kill switch is visible on every screen and reachable by keyboard (WCAG 2.1 AA, focus
  visible, contrast ≥ 4.5:1).
- No endpoint exposes an exchange credential, a full API key, or a secret; a test asserts
every response body contains no value from the secret store. The gateway also has no
outbound path to a venue: `close` and `stop` reach the exchange only through Execution, and
a test asserts the gateway image has no venue credential and no order-endpoint egress.
- Every mutating request writes an `audit_record` **before** returning success; if the audit
  write fails, the request fails.
