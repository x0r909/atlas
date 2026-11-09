# Runbook: reconcile mismatch

`reconcile` compares, every 5 minutes and per venue, what the database believes against what
the venue reports: open orders, open positions, balances. A mismatch is never "fixed" by
overwriting one side with the other. It halts new entries and asks a human.

## What the system already did when you arrive

- published `atlas:reconcile.result` with the per-venue diff;
- blocked new entries (`RECONCILIATION_MISMATCH` on the risk path) while exits stay available;
- opened an incident (`POSITION_MISMATCH` / `RECONCILE_DELAY`) with the diff in `detail`;
- if the venue had a position the database did not, adopted it, handed it to the stop monitor
  as stop-less, and expected a protective stop within the monitor's first cycle. If that stop
  did not land, the system is already `HALTED` with `SAFETY/ADOPTED_POSITION_UNPROTECTED`.

## First five minutes

1. **Protect, do not explain.** For every adopted or mismatched position, confirm a live
   protective stop exists at the venue. That is the only urgent item; the accounting can wait.
2. Read the diff:

       psql "$ATLAS_DB_DSN" -c "SELECT kind, severity, auto_action, detail, opened_at
                                FROM incident WHERE kind IN ('POSITION_MISMATCH','RECONCILE_DELAY')
                                ORDER BY opened_at DESC LIMIT 5;"

3. Decide which side is wrong. The usual three:

   | Symptom | Usual cause | Correct move |
   |---|---|---|
   | Venue has a position, we do not | a fill whose event was lost, or a manual order | adopt, then stop-protect within the cycle |
   | We have a position, venue does not | a close we recorded that the venue rejected | re-issue the reduce-only close; never mark it closed by hand |
   | Quantities differ | partial fill not yet processed | wait one reconcile cycle before acting |

4. Check for an `UNKNOWN` order before assuming a lost fill. An `UNKNOWN` order blocks new
   entries on that venue on purpose; the resolution loop looks it up by `client_order_id` and
   escalates to `HALTED` after three failures.

## Correcting

Only two writes are legitimate:

- **adopt**: insert the position discovered at the venue, `stop_confirmation = UNCONFIRMED`
  until the monitor's stop is acknowledged;
- **close**: a reduce-only order through the execution path, with its audit record.

Anything else — editing `position.quantity`, deleting a row, inserting a fill — corrupts the
audit chain's meaning even where the database would allow the write.

## Closing the incident

Close the incident only when the venue and the database agree in two consecutive cycles, and
record the root cause and remediation in the incident row. Then let the next reconcile clear
`RECONCILIATION_MISMATCH`; if it does not, the divergence is still live and the halt was
correct.
