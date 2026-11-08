"""Redis Streams transport: bounded streams, consumer groups, poison-event handling.

Every stream has an explicit ``MAXLEN``. Redis runs ``maxmemory-policy noeviction`` (see
infra/compose/docker.yml), so an unbounded stream does not degrade: it stops the whole
system writing. The table below is therefore the only place a stream name is legal, and
publishing to an unregistered stream is a programming error, not a warning.

Delivery is at-least-once. Duplicate-safe handlers key off ``event_id``; ``EventDeduper``
tracks what this consumer already applied.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from atlas_schemas.events import EventEnvelope

__all__ = [
    "DEADLETTER_STREAM",
    "MAX_DELIVERIES",
    "PENDING_IDLE_MS",
    "STREAM_MAXLEN",
    "STREAM_PRODUCERS",
    "Delivery",
    "EventBus",
    "EventDeduper",
    "ProducerNotAllowed",
    "StreamNotRegistered",
]

# contracts/event-bus.md: the authoritative stream table. Adding a stream here without the
# contract is a schema change; adding a publisher without STREAM_PRODUCERS is refused.
STREAM_MAXLEN: dict[str, int] = {
    "atlas:market.tick": 100_000,
    "atlas:market.bar": 50_000,
    "atlas:market.health": 1_000,
    "atlas:news.raw": 5_000,
    "atlas:news.enriched": 5_000,
    "atlas:feature.cycle": 1_000,
    "atlas:cycle.started": 2_000,
    "atlas:cycle.finished": 2_000,
    "atlas:proposal.created": 1_000,
    "atlas:risk.verdict": 1_000,
    "atlas:order.signed": 1_000,
    "atlas:order.update": 5_000,
    "atlas:position.update": 5_000,
    "atlas:stopmonitor.heartbeat": 1_000,
    "atlas:stopmonitor.action": 2_000,
    "atlas:reconcile.result": 1_000,
    "atlas:mode.changed": 500,
    "atlas:killswitch": 500,
    "atlas:incident": 2_000,
    "atlas:deadletter": 10_000,
}

STREAM_PRODUCERS: dict[str, frozenset[str]] = {
    "atlas:market.tick": frozenset({"ingest-market"}),
    "atlas:market.bar": frozenset({"ingest-market"}),
    "atlas:market.health": frozenset({"ingest-market"}),
    "atlas:news.raw": frozenset({"ingest-news"}),
    "atlas:news.enriched": frozenset({"feature-engine"}),
    "atlas:feature.cycle": frozenset({"feature-engine"}),
    "atlas:cycle.started": frozenset({"risk-engine"}),
    "atlas:cycle.finished": frozenset({"risk-engine"}),
    "atlas:proposal.created": frozenset({"agents"}),
    "atlas:risk.verdict": frozenset({"risk-engine"}),
    "atlas:order.signed": frozenset({"risk-engine"}),
    "atlas:order.update": frozenset({"execution"}),
    "atlas:position.update": frozenset({"execution"}),
    "atlas:stopmonitor.heartbeat": frozenset({"stop-monitor"}),
    "atlas:stopmonitor.action": frozenset({"stop-monitor"}),
    "atlas:reconcile.result": frozenset({"execution"}),
    "atlas:mode.changed": frozenset({"api-gateway"}),
    "atlas:killswitch": frozenset({"api-gateway", "telegram-bot"}),
    "atlas:incident": frozenset({"api-gateway", "execution", "risk-engine", "stop-monitor"}),
    "atlas:deadletter": frozenset({"*"}),
}

DEADLETTER_STREAM = "atlas:deadletter"
MAX_DELIVERIES = 3
PENDING_IDLE_MS = 5 * 60 * 1000


class StreamNotRegistered(ValueError):
    """Raised for a stream name that is not in STREAM_MAXLEN."""


class ProducerNotAllowed(PermissionError):
    """Raised when a producer publishes to a stream the contract does not grant it."""


@dataclass(frozen=True, slots=True)
class Delivery:
    stream: str
    msg_id: str
    envelope: EventEnvelope | None
    deliveries: int = 1
    decode_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.envelope is not None


class EventDeduper:
    """At-least-once becomes effectively-once: an event_id is applied at most once."""

    def __init__(
        self,
        ttl_s: float = 3600.0,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_s
        self._clock = clock
        self._seen: dict[str, float] = {}

    def first_seen(self, event_id: str) -> bool:
        now = self._clock()
        expired = [key for key, at in self._seen.items() if now - at > self._ttl]
        for key in expired:
            del self._seen[key]
        if event_id in self._seen:
            return False
        self._seen[event_id] = now
        return True


class EventBus:
    """Thin wrapper over a redis.asyncio client; the client is injected so tests need none."""

    def __init__(
        self,
        redis: Any,
        *,
        group: str = "atlas",
        consumer: str = "worker",
    ) -> None:
        self._redis = redis
        self._group = group
        self._consumer = consumer

    # --- publishing -------------------------------------------------------------------
    @staticmethod
    def check_stream(stream: str) -> None:
        if stream not in STREAM_MAXLEN:
            raise StreamNotRegistered(
                f"stream {stream!r} has no MAXLEN; register it in STREAM_MAXLEN from "
                "contracts/event-bus.md before publishing"
            )

    @staticmethod
    def check_producer(stream: str, producer: str) -> None:
        allowed = STREAM_PRODUCERS.get(stream)
        if allowed is None:
            raise StreamNotRegistered(f"stream {stream!r} has no producer allowlist")
        if "*" in allowed or producer in allowed:
            return
        raise ProducerNotAllowed(
            f"{producer!r} may not publish to {stream!r}; allowed: {sorted(allowed)}"
        )

    async def publish(self, stream: str, envelope: EventEnvelope) -> str:
        self.check_stream(stream)
        self.check_producer(stream, envelope.producer)
        maxlen = STREAM_MAXLEN[stream]
        return str(
            await self._redis.xadd(
                stream,
                envelope.redis_fields(),
                maxlen=maxlen,
                approximate=True,
            )
        )

    async def publish_event(
        self,
        stream: str,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        producer: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        venue_id: str | None = None,
    ) -> str:
        envelope = EventEnvelope.create(
            event_type,
            producer,
            dict(payload),
            correlation_id=correlation_id,
            causation_id=causation_id,
            venue_id=venue_id,
        )
        return await self.publish(stream, envelope)

    # --- consuming --------------------------------------------------------------------
    async def ensure_group(self, stream: str, *, start_id: str = "0") -> None:
        self.check_stream(stream)
        try:
            await self._redis.xgroup_create(stream, self._group, id=start_id, mkstream=True)
        except Exception as exc:  # redis raises ResponseError BUSYGROUP when it exists
            if "BUSYGROUP" not in str(exc):
                raise

    async def read(
        self,
        stream: str,
        *,
        count: int = 10,
        block_ms: int | None = 1000,
        consumer: str | None = None,
    ) -> list[Delivery]:
        self.check_stream(stream)
        response = await self._redis.xreadgroup(
            self._group,
            consumer or self._consumer,
            streams={stream: ">"},
            count=count,
            block=block_ms,
        )
        return self._decode(stream, response)

    async def claim_stale(
        self,
        stream: str,
        *,
        min_idle_ms: int = PENDING_IDLE_MS,
        count: int = 50,
        consumer: str | None = None,
    ) -> list[Delivery]:
        """Take over messages a dead consumer left pending (XAUTOCLAIM)."""
        self.check_stream(stream)
        result = await self._redis.xautoclaim(
            stream,
            self._group,
            consumer or self._consumer,
            min_idle_time=min_idle_ms,
            start_id="0-0",
            count=count,
        )
        # redis-py: (next_cursor, messages, deleted_ids); older versions return two items.
        messages = result[1] if len(result) > 1 else []
        return self._decode(stream, [(stream, messages)])

    async def ack(self, stream: str, msg_id: str) -> int:
        return int(await self._redis.xack(stream, self._group, msg_id))

    async def delivery_count(self, stream: str, msg_id: str) -> int:
        entries = await self._redis.xpending_range(
            stream, self._group, min=msg_id, max=msg_id, count=1
        )
        if not entries:
            return 0
        entry = entries[0]
        if isinstance(entry, Mapping):
            return int(entry.get("times_delivered", 1))
        return int(entry[3])

    async def deadletter(self, delivery: Delivery, error: str) -> str:
        fields = {
            "original_stream": delivery.stream,
            "original_id": delivery.msg_id,
            "error": error[:1000],
            "deliveries": str(delivery.deliveries),
            "deadlettered_at_ms": str(int(time.time() * 1000)),
        }
        if delivery.envelope is not None:
            fields |= {
                f"envelope_{key}": value for key, value in delivery.envelope.redis_fields().items()
            }
        msg_id = str(
            await self._redis.xadd(
                DEADLETTER_STREAM,
                fields,
                maxlen=STREAM_MAXLEN[DEADLETTER_STREAM],
                approximate=True,
            )
        )
        await self.ack(delivery.stream, delivery.msg_id)
        return msg_id

    async def deliver(
        self,
        delivery: Delivery,
        handler: Callable[[EventEnvelope], Awaitable[None]],
        *,
        deduper: EventDeduper | None = None,
    ) -> str:
        """Run one delivery to completion.

        Returns "acked", "duplicate", "retry" or "deadlettered". A malformed envelope can
        never succeed, so it is dead-lettered immediately instead of burning three retries.
        """
        if delivery.envelope is None:
            await self.deadletter(delivery, delivery.decode_error or "undecodable envelope")
            return "deadlettered"
        if deduper is not None and not deduper.first_seen(str(delivery.envelope.event_id)):
            await self.ack(delivery.stream, delivery.msg_id)
            return "duplicate"
        try:
            await handler(delivery.envelope)
        except Exception as exc:
            attempts = max(
                delivery.deliveries,
                await self.delivery_count(delivery.stream, delivery.msg_id),
            )
            if attempts >= MAX_DELIVERIES:
                await self.deadletter(delivery, f"{type(exc).__name__}: {exc}")
                return "deadlettered"
            return "retry"
        await self.ack(delivery.stream, delivery.msg_id)
        return "acked"

    # --- decoding ---------------------------------------------------------------------
    def _decode(self, stream: str, response: Any) -> list[Delivery]:
        deliveries: list[Delivery] = []
        if not response:
            return deliveries
        for _name, messages in response:
            for msg_id, fields in messages:
                try:
                    envelope = EventEnvelope.from_redis_fields(fields)
                except Exception as exc:
                    deliveries.append(
                        Delivery(
                            stream=stream,
                            msg_id=str(msg_id),
                            envelope=None,
                            decode_error=f"{type(exc).__name__}: {exc}",
                        )
                    )
                    continue
                deliveries.append(Delivery(stream=stream, msg_id=str(msg_id), envelope=envelope))
        return deliveries
