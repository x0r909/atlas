# Contract: Telegram Remote Kill Switch

**Owner**: `services/api-gateway` (bot bridge)
**Audience**: the operator's phone

Telegram is the out-of-band control channel: it must work when the dashboard, the agents,
or the database read path is degraded. It is a **control** channel, never a trading channel.

## Authentication

1. Bot token is a secret (sops+age) and never logged.
2. Incoming update must pass **all** of:
   - `message.from.id ∈ TELEGRAM_ALLOWLIST` (a set, currently one id);
   - chat id ∈ allowlist;
   - `TOTP` code valid for the current 30 s window, or the operator PIN (argon2 hash,
     separate env secret) as fallback;
   - the update arrived within 60 s of its `date` field (replay window).
3. Failed auth: no action, count incremented, three failures in 10 min ⇒ the bot posts a
   SEV2 incident and ignores that chat for 15 min. Every attempt is audited (id, result,
   reason) with the message text truncated.

## Commands

| Command | Effect | Confirmation |
| --- | --- | --- |
| `/status` | mode, equity, open positions, drawdown, spend, data freshness | none |
| `/halt <totp>` | halt new entries, keep managing open positions | single step |
| `/stopall <totp> <PIN>` | halt + close all positions market-wise | two factors, explicit |
| `/mode <PAPER\|CO_PILOT\|AUTONOMOUS> <totp>` | request a mode change | refuses promotion unless a passing evaluation exists; replies with the exact evidence summary |
| `/confirm <code>` | confirm a pending request (`/mode`, `/stopall`) | code valid 60 s, single use |
| `/limits` | show active risk config | none |
| `/incidents` | open incidents | none |
| `/killtest` | kill-switch drill: asserts the path without placing orders | none, recorded as a drill |

`/killtest` exists because FR-059 requires kill-switch drills as promotion evidence; it
publishes `atlas:killswitch` with `dry_run=true`, asserts every consumer acknowledged it, and
records a drill row.

## Behaviour requirements

- **Latency**: publish to Redis within 1 s of receiving a valid command; total
  command → `execution` acknowledgement p95 ≤ 2 s (network to Telegram included, not
  guaranteed).
- **Idempotency**: every command carries the Telegram `update_id`; a repeated update id is
  ignored. `/halt` twice is a no-op, not an error.
- **No database dependency for the halt path**: write `atlas:killswitch` first, then attempt
  the audit insert, then reply. If the audit insert fails, the halt still stands and the bot
  says so explicitly.
- **No free text**: the bot only accepts the commands above; any other text is ignored and
  counted. There is no natural-language path into the system (Principle VI).
- **Replies are data, not instructions**: status replies contain numbers only plus fixed
  templates. News headlines are never echoed into the chat.
- **Outage mode**: if Redis is unreachable, the bot replies
  `CONTROL PLANE UNAVAILABLE — use dashboard` and raises a SEV1 incident. It never claims
  success it cannot verify.
- **Silence rule**: if the scheduler has not produced a heartbeat for 20 minutes, the bot
  sends an unsolicited alert (dead-man switch). A dead system must not look quiet.

## Audit fields

`audit_record(record_type=KILL_SWITCH|MODE_CHANGE, payload={source:"telegram", update_id,
telegram_user_id, command, totp_verified:true, pin_verified:bool, result,
acknowledged_by:[services], latency_ms})`.

## Tests

- Allowlisted user + valid TOTP ⇒ halt event on the bus in < 1 s (fakeredis).
- Non-allowlisted id ⇒ no event, audit row with `result=DENIED`.
- Replayed `update_id` ⇒ exactly one event.
- Stale `date` (> 60 s) ⇒ denied.
- Redis down ⇒ error reply, no false success, SEV1 incident.
- `/stopall` without PIN ⇒ refused, nothing closed.
- `/mode AUTONOMOUS` without a passing evaluation ⇒ refused with the failing criteria listed.
