# Quickstart — Validation Scenarios

These scenarios are the acceptance path for M0–M3. Each one is runnable on a laptop with
Docker Compose and no exchange credentials unless stated. They prove the wiring; they are
not a replacement for the test suite.

Prerequisites: Docker (or Podman) + Compose v2, Python 3.12, Node 22, `sops` + `age`, and a
`.env` produced by `infra/secrets/decrypt.sh dev` (the dev file contains **no** live keys).

```bash
# 1. bring up dependencies only
docker compose -f infra/compose/docker-compose.dev.yml up -d postgres redis prometheus grafana

# 2. migrate + seed the dev venue profile (Indodax fixture data, not live)
alembic upgrade head
python -m atlas.seed --venue indodax --profile fixtures/indodax_profile_v1.json

# 3. run the paper loop against recorded data (no network)
docker compose -f infra/compose/docker-compose.dev.yml up agents risk-engine execution api-gateway
```

---

## S1 — Fresh install defaults to PAPER (FR-001, constitution IV)

```bash
docker compose up -d            # no ATLAS_MODE set at all
curl -s localhost:8080/api/mode | jq .mode      # => "PAPER"
ATLAS_MODE=not-a-mode docker compose up -d
curl -s localhost:8080/api/mode | jq .mode      # => "PAPER"
curl -s localhost:8080/api/mode | jq '.history[0].reason_code'   # => "STARTUP_DEFAULT"
```

**Pass**: mode is `PAPER` in both cases and the audit history records `STARTUP_DEFAULT`.

## S2 — Market data freshness and the stale breaker (FR-016, FR-021)

```bash
python -m atlas.tools.freeze_feed --pair btc_idr     # stop the ingest WS
sleep 15
curl -s localhost:8080/api/health | jq '.data'       # status: "STALE", age_ms > 10000
```

Then trigger a cycle and inspect the verdict:

```bash
python -m atlas.tools.run_cycle --force
curl -s localhost:8080/api/decisions/latest | jq '.verdict.primary_rule_id'
```

**Pass**: `"STALE_PRICE"`, no order row created, and the terminal shows the stale badge.

## S3 — Risk Engine rejects a proposal below the venue minimum (FR-051)

```bash
python -m atlas.tools.simulate --pair btc_idr --entry 1000000000 --stop 990000000 --target 1030000000
```

**Pass**: verdict `approved=false`, `primary_rule_id="MIN_ORDER_EXCEEDS_RISK_BUDGET"`,
`rule_results` contains the observed notional and the Rp10.000 limit. Then widen the capital
to a test equity of Rp5.000.000 via the dev config and repeat: the verdict flips to
`approved=true` with `sized_notional_idr` between 10.000 and 25% of equity.

## S4 — R:R after costs is enforced (FR-058)

```bash
python -m atlas.tools.simulate --pair btc_idr --entry 1000 --stop 980 --target 1020 --equity 5000000
```

**Pass**: `approved=false`, `primary_rule_id="RR_BELOW_MIN"`, `rr_net < 1.5`, and
`cost_breakdown.total_round_trip_idr > 0`. Change `--target 1080` and the same call approves.

## S5 — One order per verdict, idempotent under retry (FR-031, FR-032)

```bash
python -m atlas.tools.simulate --pair btc_idr --entry 1000 --stop 980 --target 1080 --equity 5000000 --execute
curl -s localhost:8080/api/orders | jq 'length'          # 1
python -m atlas.tools.resubmit_same_verdict             # replay the identical signed order
curl -s localhost:8080/api/orders | jq 'length'          # still 1
curl -s localhost:8080/api/audit | jq '[.records[] | select(.record_type=="ORDER")] | length'  # 1
```

**Pass**: exactly one order, one audit record, and the second submit returns
`{"deduplicated": true}`.

## S6 — LLM budget cap degrades instead of trading (FR-052)

```bash
python -m atlas.tools.spend_simulate --idr 5000        # exhaust today's cap
python -m atlas.tools.run_cycle --force
curl -s localhost:8080/api/spend | jq '{today, cap, stage}'   # stage: "THROTTLED_ENTRIES"
curl -s localhost:8080/api/decisions/latest | jq '.outcome'   # "BUDGET_BLOCKED"
curl -s localhost:8080/api/positions?status=open | jq 'length'  # unchanged
```

**Pass**: no new position, management/stops still running, dashboard shows the cap, and the
kill switch still responds (`/killtest` passes).

## S7 — Kill switch works with the agents stopped (FR-005, constitution V)

```bash
docker compose stop agents
curl -s -X POST localhost:8080/api/killswitch -H 'X-Atlas-Confirm: <token>' \
  -d '{"scope":"NEW_ENTRIES","close_positions":false,"reason":"drill"}'
sleep 2
curl -s localhost:8080/api/orders?status=open | jq 'length'   # entries canceled
```

Telegram path: `/killtest` then `/halt <TOTP>` from the allowlisted account.

**Pass**: entries cancelled within 5 s, `atlas:killswitch` acknowledged by `execution`,
audit rows in both channels, agents still down and nothing else broken.

## S8 — Reconciliation mismatch blocks new entries (FR-034)

```bash
python -m atlas.tools.inject_mismatch --pair eth_idr --delta 0.001
python -m atlas.tools.run_cycle --force
curl -s localhost:8080/api/decisions/latest | jq '.verdict.primary_rule_id'
```

**Pass**: `"RECONCILIATION_MISMATCH"`, incident opened (`class=SAFETY`,
`kind=POSITION_MISMATCH`), and new entries stay blocked until
`python -m atlas.tools.resolve_mismatch` clears it.

## S9 — Mode promotion refuses without evidence (FR-059)

```bash
curl -s -X POST localhost:8080/api/mode -d '{"to":"CO_PILOT","reason":"try"}' \
  -H 'X-Atlas-Confirm: <token>'
```

**Pass**: HTTP 400 `MODE_TRANSITION_INVALID` with the failing criteria listed (days, cycles,
trades, drawdown, drills), mode unchanged. After seeding a passing evaluation:
`POST /promotion/evaluate` then `/promotion/confirm` then `/mode` succeeds, and the new
`mode_state` row references the evaluation id.

## S10 — Replay is byte-identical (FR-039, constitution VII)

```bash
python -m atlas.tools.replay --decision <decision_id> --assert-identical
```

**Pass**: exit 0, verdict rules, approval, and signed-order payload identical to the stored
record. Tamper one input field in the record and the command must exit 1 with the diff.

## S11 — Audit hash chain integrity (FR-037, constitution VII)

```bash
python -m atlas.tools.verify_chain        # "chain ok: N records"
psql $DATABASE_URL -c "update audit_record set payload='{}' where seq=10"   # dev only
python -m atlas.tools.verify_chain        # exit 1, names seq 10, opens SEV1
```

## S12 — Terminal live update latency (SC/performance goal)

With `terminal-web` open on BTC/IDR: publish a synthetic tick and measure
`performance.now()` from socket message to painted price. **Pass**: ≤ 1 s on the local
compose network, no layout shift, kill switch still focusable (Tab from page load reaches it
in ≤ 5 tabs).

---

## S13 — Verdict expiry and single use (FR-062, SC-020)

```bash
python -m atlas.tools.simulate --pair btc_idr --entry 1000 --stop 980 --target 1080 --equity 5000000 --hold-verdict 70 --execute
# -> refused: VERDICT_EXPIRED, 0 orders on the venue, 1 audit record with that reason
python -m atlas.tools.replay_consumed_verdict      # same verdict_id, still inside its 60 s window
# -> refused: VERDICT_CONSUMED
python -m atlas.tools.tamper_verdict --quantity 2x # same signature, altered payload
# -> refused: VERDICT_PAYLOAD_MISMATCH
```

**Pass**: all three attempts rejected before any venue call (no outbound request in the
recorded log), each with its own stable reason code, and order count unchanged.

## S14 — Exits are never blocked by halt, breaker, or budget (FR-063, SC-021)

```bash
curl -s -X POST localhost:8080/api/killswitch -H "X-Atlas-Confirm: $TOKEN" -d '{"scope":"ENTRIES_ONLY"}'
python -m atlas.tools.run_cycle --force          # agent proposes an entry
python -m atlas.tools.close_position --all       # operator/system close
python -m atlas.tools.place_protective_stop --all
curl -s localhost:8080/api/orders | jq '[.orders[] | select(.side=="BUY")] | length'   # unchanged
```

**Pass**: zero new BUY orders while halted, while the close and protective-stop sells are
accepted and filled in the simulator; repeat with the spend cap exhausted and with a breaker
active — same result.

## S15 — Stop monitor heartbeat loss forces HALTED (FR-067, SC-022)

```bash
python -m atlas.tools.open_position --pair btc_idr --qty 0.01 --stop 980
docker compose stop stop-monitor
sleep 35
curl -s localhost:8080/api/mode | jq '.mode'            # "HALTED"
curl -s localhost:8080/api/incidents | jq '.[0] | {class, kind}'   # SAFETY / STOP_MONITOR_UNRESPONSIVE
curl -s localhost:8080/api/orders | jq '[.orders[] | select(.reduce_only)] | length'  # >= 1
```

**Pass**: HALTED within 5 s of the 30 s threshold, exactly one close attempt per position,
alert delivered, and restarting the monitor does **not** lift HALTED on its own.

## S16 — Partial fill leaves no unprotected position (FR-068, SC-023)

```bash
python -m atlas.tools.simulate_fill --order <id> --filled 40% --delay 5
sleep 12   # one monitor cycle
curl -s localhost:8080/api/positions | jq '.[0] | {quantity, stop_quantity, stop_order_id}'
sleep 45
curl -s localhost:8080/api/orders/<id> | jq '.status'   # "CANCELED" with filled_quantity kept
```

**Pass**: stop quantity equals the filled quantity after one cycle, remainder cancelled after
the 45 s timeout, and no snapshot exists where the position is open without a stop.

## S17 — UNKNOWN order freezes the venue, never resubmits (FR-069, SC-024)

```bash
python -m atlas.tools.simulate_timeout --on place_order     # first call times out
curl -s localhost:8080/api/orders | jq '.[0].status'        # "UNKNOWN"
python -m atlas.tools.run_cycle --force                     # proposes another entry
curl -s localhost:8080/api/orders | jq '[.orders[] | select(.side=="BUY")] | length'  # 1
python -m atlas.tools.simulate_timeout --on get_order --times 3
# -> HALTED, incident VENUE_UNRESOLVED_ORDER
```

**Pass**: no second `place_order` in the recorded venue request log, no new entry while the
order is `UNKNOWN`, and three failed resolutions halt the system with an alert.

## S18 — Failing close escalates instead of hiding (FR-070, SC-025)

```bash
python -m atlas.tools.simulate_reject --on close --times 2
python -m atlas.tools.close_position --all
curl -s localhost:8080/api/incidents | jq '[.[] | select(.kind=="CLOSE_FAILED")] | length'  # >= 1
curl -s localhost:8080/api/notifications | jq 'length'   # operator alert after 2nd failure
python -m atlas.tools.close_position --all               # 3rd attempt
curl -s localhost:8080/api/orders | jq '[.orders[] | select(.type=="MARKET")] | length'  # 1
```

**Pass**: alert sent by the second failure, new entries stopped until the position is closed,
and the third attempt uses a market order inside the slippage guard.

---

## M3 exit criteria (what "paper mode works" means)

- S1–S18 pass in CI except S12, which is manual and screenshot-recorded.
- 72 h continuous paper run: zero unhandled exceptions, zero orders without a stop, every
  cycle has a `decision_record`, daily LLM spend ≤ Rp5.000, and a fresh VPS reboot resumes
  in `PAPER` with reconciled state.
- Chaos suite green: stale data, venue timeout, LLM timeout, invalid model output, rejected
  order, partial fill, stop placement failure, stop-monitor kill, `UNKNOWN` resolution,
  repeated close failure.
