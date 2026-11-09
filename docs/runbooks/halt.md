# Runbook: halt

The kill switch exists so that stopping the system never depends on the part of the system
that is misbehaving. Read this before you need it.

## What "halted" means

`mode_state` moves to `HALTED` and stays there. In `HALTED`:

- no new entry order is accepted (`MODE_NOT_TRADABLE` on the execution path, one audit record,
  zero venue requests);
- **exits still run**: closing a position, moving a stop tighter, and placing a missing
  protective stop all reach the venue. Nothing about a halt may delay an exit;
- the agents loop keeps cycling but cannot emit a trade (`NO_TRADE` only).

Halt is a state, not an action: it does not cancel open orders by itself, and it does not
close positions. Protective stops already sitting at the venue keep protecting them.

## Halt

Dashboard: press **Halt** — it is the first focusable element on every route, needs only a
session, and never asks for a confirmation token. Telegram: `/halt`. Both paths write the
Redis `atlas:killswitch` message **before** the database row, so a halt still lands when the
database is the thing that is broken.

Order of operations, and why:

1. publish `atlas:killswitch` (agents stop immediately, even if they never read the DB);
2. append the `KILL_SWITCH` audit record;
3. append the `mode_state` row (`HALTED`, `KILL_SWITCH`, actor `operator:<name>`).

## Confirm it took

    psql "$ATLAS_DB_DSN" -c "SELECT seq, mode, previous_mode, reason_code, actor, created_at
                             FROM mode_state ORDER BY seq DESC LIMIT 1;"

Expect `HALTED` with `reason_code = 'KILL_SWITCH'`. Then check that the loop stopped proposing:

    redis-cli -a "$REDIS_PASSWORD" XLEN atlas:proposal.created

The count must stop climbing. If it does not, the agents process is not reading the bus: stop
the container, not the account.

## While halted

- Do nothing that increases exposure. Manual venue orders are outside the audit chain.
- Investigate before lifting. Every halt has a reason in `incident` or in the audit record;
  find it, because lifting without one just re-arms the same failure.
- A halt caused by `STOP_MONITOR_UNRESPONSIVE`, `ADOPTED_POSITION_UNPROTECTED`, or
  `ORDER_WITHOUT_STOP` is a **safety** halt: an operator must confirm by hand that every open
  position has a live protective stop at the venue before lifting.

## Lift

    POST /halt/lift   {"target_mode": "PAPER", "reason": "..."}   + X-Atlas-Confirm

Lifting needs a session, a fresh confirmation token, and a reason. `HALTED` may only return to
`PAPER` or `CO_PILOT`; there is no path from `HALTED` straight to `AUTONOMOUS`, and no path
from `PAPER` to `AUTONOMOUS` at all. Restarting a service never lifts a halt — the mode is
data, not process state.

## Do not

- Do not `UPDATE mode_state`. It is append-only; the trigger refuses, and a chain that skips a
  mode change is a security incident of its own.
- Do not disable the watchdog or the stop monitor to "stop the noise". They are what turns a
  quiet problem into a halt.
- Do not lift and immediately place an order to "test" the venue. Use Paper.
