# Runbook: promotion

Stages are `PAPER → CO_PILOT → AUTONOMOUS`, and no step of that ladder is automatic. Backtest
and Paper can run for years without promoting anything; the ladder is entered by a human who
has read the evidence.

## What a promotion is not

It is not a config change, and it is not the same as changing a limit. Promotion moves real
capital onto an unproven system, so it has its own path: `POST /promotion/evaluate`, then a
fresh re-authentication, then `POST /promotion/confirm`.

## Criteria (FR-059)

All of these must hold for the stage being requested, and the evaluation must be younger than
24 hours when it is used:

| Stage | Minimum evidence |
|---|---|
| `CO_PILOT` | ≥ 4 weeks of Paper, ≥ 100 closed trades, net-positive after costs, zero safety incidents in the last 2 weeks, every order had a protective stop |
| `AUTONOMOUS` | ≥ 8 weeks of `CO_PILOT` with operator review of every proposal, ≥ 60 operator approvals of the same shape the agent proposes, drawdown within the configured cap, no unresolved `SEV1` |

The criteria are evaluated from the database, not from a spreadsheet. A failing criterion is
named in the response; a passing evaluation expires.

## Prepare

1. Resolve or accept every open incident. A `SEV1` blocks promotion, full stop.
2. Confirm the runtime database identity gate (research.md R17): the service connects as a
   non-owner role with only `SELECT`/`INSERT` on the append-only tables, proven by
   `tests/integration/test_db_role_privileges.py`. Promotion is refused otherwise — the whole
   guarantee that a halt cannot be edited away rests on it.
3. For venue Live, and only then: the venue key must have withdrawals disabled and an active
   IP allowlist (FR-036), and the venue profile must be `VERIFIED` — a Live verdict may never
   price a cost from a `*_assumed` column (see `COST_MODEL_INCOMPLETE`).
4. `GET /promotion/evaluate` and read every criterion, including the ones that passed.

## Promote

    POST /promotion/confirm  {"evaluation_id": "...", "target_mode": "CO_PILOT"}
    + X-Atlas-Confirm: <token>
    + X-Atlas-Step-Up: <TOTP or passkey assertion>

The confirmation is single-use per evaluation, requires a step-up re-authentication within the
preceding 5 minutes, and writes its audit record **before** returning success. An evaluation
older than 24 hours, already used, or carrying any failing criterion is refused.

After promoting to `CO_PILOT`, the agent still proposes and a human still confirms each trade;
the capital cap applies (≤ Rp30.000 per position) until `AUTONOMOUS`.

## Demote

Demotion is always allowed, needs no evaluation, and is the correct response to "I am not sure
this is working". `POST /mode` with the confirm token, or `/halt` in an emergency.

## Never

- Never set `mode_state` by hand, and never edit `promotion_evaluation`.
- Never promote on the strength of a backtest alone: the ladder exists because Paper and
  Backtest share no failure mode with a venue that rejects, delays, or double-fills.
- Never promote while an `UNKNOWN` order is outstanding.
