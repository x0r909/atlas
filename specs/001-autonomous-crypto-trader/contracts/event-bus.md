# Contract: Event Bus (Redis Streams)

**Owner**: `packages/common/streams.py`
**Consumers**: all services

Redis Streams is the only inter-service transport. No service calls another service's HTTP
API for internal flow (the API gateway is for the operator, not for services). This keeps
the order path free of synchronous cross-service coupling.

## Envelope (every message)

```python
class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID            # ULID-ish, sortable
    event_type: str           # see topic table
    schema_version: int       # 1
    produced_at: int          # epoch ms, synchronized clock
    producer: str             # service name
    correlation_id: str       # cycle_id when available, else event_id
    causation_id: str | None  # event_id that caused this one
    venue_id: str | None
    payload: dict             # validated against the topic schema
```

Redis fields: `event_id` (also the message id), `type`, `v`, `ts`, `producer`, `corr`,
`cause`, `venue`, `payload` (canonical JSON). Consumers re-validate `payload` against the
pydantic model for that `event_type` and **drop + count** anything that fails
(`atlas_bus_invalid_total`), because a schema drift between deploy versions must not crash
a consumer mid-cycle.

## Topics

| Stream | Producer | Consumers (group) | Retention |
| --- | --- | --- | --- |
| `atlas:market.tick` | ingest-market | feature-engine, api-gateway | `MAXLEN ~ 100_000` |
| `atlas:market.bar` | ingest-market | feature-engine, api-gateway | `MAXLEN ~ 50_000` |
| `atlas:market.health` | ingest-market | api-gateway, agents | `MAXLEN ~ 1_000` |
| `atlas:news.raw` | ingest-news | feature-engine, api-gateway | `MAXLEN ~ 5_000` |
| `atlas:news.enriched` | feature-engine | agents, api-gateway | `MAXLEN ~ 5_000` |
| `atlas:feature.cycle` | feature-engine | agents | `MAXLEN ~ 1_000` |
| `atlas:cycle.started` / `atlas:cycle.finished` | agents | api-gateway, execution | `MAXLEN ~ 2_000` |
| `atlas:proposal.created` | agents | risk-engine, api-gateway | `MAXLEN ~ 1_000` |
| `atlas:risk.verdict` | risk-engine | execution, api-gateway | `MAXLEN ~ 1_000` |
| `atlas:order.signed` | risk-engine | execution | `MAXLEN ~ 1_000` |
| `atlas:order.update` | execution | api-gateway, agents, stop-monitor | `MAXLEN ~ 5_000` |
| `atlas:position.update` | execution | api-gateway, agents, risk-engine, stop-monitor | `MAXLEN ~ 5_000` |
| `atlas:stopmonitor.heartbeat` | stop-monitor | api-gateway, execution | `MAXLEN ~ 1_000` |
| `atlas:stopmonitor.action` | stop-monitor | execution, api-gateway | `MAXLEN ~ 2_000` |
| `atlas:reconcile.result` | execution | risk-engine, api-gateway | `MAXLEN ~ 1_000` |
| `atlas:mode.changed` | api-gateway | **all** (fan-out) | `MAXLEN ~ 500` |
| `atlas:killswitch` | api-gateway | execution, agents, risk-engine | `MAXLEN ~ 500` |
| `atlas:incident` | any | api-gateway | `MAXLEN ~ 2_000` |

## Delivery semantics

- **At-least-once** with consumer groups (`XREADGROUP`), explicit `XACK` only after the
  side effect is durable. Duplicate delivery is expected and must be idempotent:
  - `order.*` handlers key on `client_order_id`;
  - `risk.verdict` handlers key on `verdict_id`;
  - `stopmonitor.action` handlers key on `(position_id, stop_generation)`;
  - `position.update` handlers key on `(venue_id, pair, seq)`.
- Pending-entry list is inspected at startup; entries older than 5 minutes are claimed via
  `XAUTOCLAIM` by the same consumer rather than left to starve.
- Poison messages: after 3 failed deliveries → move to `atlas:deadletter`, emit
  `atlas:incident` with `class=OPERATIONAL, kind=POISON_EVENT`, and continue. A dead letter
  never blocks the stream.
- Ordering guarantee: ordering is per stream and per producer only. Consumers that need
  causal order use `venue_ts` and the stored `seq`, never arrival order.

## Producer allowlist and bus authorization (FR-064)

Redis is shared by every service, so "only `execution` mutates orders" cannot rest on
trusting the publisher. Two layers apply, and the cheap one is not the only one:

1. **Consumer-side allowlist (mandatory, in-process).** Each protected stream declares the
   producers it will accept. An envelope whose `producer` is not listed is dropped **before**
   payload validation, counted in `atlas_bus_rejected_total{stream,producer}`, and recorded as
   one `SAFETY` audit record naming the stream, the claimed producer, and the `event_id`.

   | Stream | Accepted producers |
   | --- | --- |
   | `atlas:risk.verdict`, `atlas:order.signed` | `risk-engine` |
   | `atlas:stopmonitor.heartbeat`, `atlas:stopmonitor.action` | `stop-monitor` |
   | `atlas:order.update`, `atlas:position.update`, `atlas:reconcile.result` | `execution` |
   | `atlas:mode.changed`, `atlas:killswitch` | `api-gateway` |
   | `atlas:proposal.created` | `agents` |
   | `atlas:market.*` | `ingest-market` |
   | `atlas:news.raw` | `ingest-news` |
   | `atlas:news.enriched`, `atlas:feature.cycle` | `feature-engine` |

2. **HMAC payload signature (mandatory, independent).** Allowlisting narrows who may speak;
   the signing in `contracts/decision-risk.md` still decides whether the content is
   authentic. A `risk-engine`-labelled envelope with a forged or copied signature is refused
   at the gate, so compromising the risk-engine process alone still cannot mint an order.

3. **Redis ACL per stream (deployment hardening, compose and VPS).** Each service connects
   with its own Redis user: `+xadd` only for the streams it produces, `+xreadgroup`/`+xclaim`
   only for the streams it consumes, `+xack` on its group, and no `+xdel`/`+xtrim`/
   `+flushall`/`+config`. A service that tries to `XADD` on a stream it does not produce gets
   `NOPERM`. This layer is a second lock, never the primary control: the in-process allowlist
   must still hold if ACLs are misconfigured or a password leaks inside the compose network.

Rejection is a safety event, not a crash: the consumer continues, the audit chain records
the attempt, and an operator alert is raised once per `(stream, producer)` per hour to avoid
an alert storm during a misconfigured deploy.

## Backpressure rules

- Producers use `XADD MAXLEN` so Redis memory is bounded (`maxmemory 384mb`,
  `maxmemory-policy noeviction`). If `XADD` fails with OOM, the producer emits a health event
  and **halts new trading** (it does not drop silently).
- Consumers process in small batches (`COUNT 32`, `BLOCK 1000`); a slow consumer must not
  create unbounded pending lists — the reconcile loop and the kill switch listen on their
  own dedicated streams so they are never behind market data.

## Mode and kill switches travel on the bus

`atlas:mode.changed` and `atlas:killswitch` are the only events that may change behaviour
without a restart. Every service checks the *current* mode from `mode_state` at the start of
each unit of work **and** subscribes to `mode.changed`; a missed event therefore cannot keep
a service trading. `execution` also polls `mode_state` every 10 s as a second line of
defense.

## Testing

Contract tests run against `redis:7` in a container (or `fakeredis` for unit tests) and
assert: envelope validation, duplicate delivery idempotency, dead-letter after 3 failures,
that `atlas:killswitch` reaches `execution` in < 1 s, and that a producer outside a stream's
allowlist is dropped, counted, audited as `SAFETY`, and never reaches a handler — while a
correctly allowlisted message with an invalid signature is still refused downstream.
