"""Tests for the Redis Streams transport. No Redis: a fake client implements the few calls
EventBus makes, so the delivery semantics are tested rather than the driver."""

from __future__ import annotations

from typing import Any

import pytest
from atlas_common.streams import (
    DEADLETTER_STREAM,
    MAX_DELIVERIES,
    STREAM_MAXLEN,
    STREAM_PRODUCERS,
    EventBus,
    EventDeduper,
    ProducerNotAllowed,
    StreamNotRegistered,
)
from atlas_schemas.events import EventEnvelope


class FakeRedis:
    """Minimal Streams model: xadd/xreadgroup/xack/xautoclaim/xpending_range/xgroup_create."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.pending: dict[tuple[str, str], dict[str, Any]] = {}
        self.maxlen_calls: list[tuple[str, int]] = []
        self._seq = 0

    async def xgroup_create(self, name, group, id="0", mkstream=False):
        self.streams.setdefault(name, [])
        return True

    async def xadd(self, name, fields, maxlen=None, approximate=True):
        self._seq += 1
        msg_id = f"{self._seq}-0"
        entries = self.streams.setdefault(name, [])
        entries.append((msg_id, dict(fields)))
        if maxlen is not None:
            self.maxlen_calls.append((name, maxlen))
            del entries[: max(0, len(entries) - maxlen)]
        return msg_id

    async def xreadgroup(self, group, consumer, streams, count=10, block=None):
        out = []
        for name in streams:
            batch = []
            for msg_id, fields in self.streams.get(name, [])[:count]:
                if (name, msg_id) in self.pending:
                    continue
                self.pending[(name, msg_id)] = {
                    "consumer": consumer,
                    "times_delivered": 1,
                }
                batch.append((msg_id, fields))
            if batch:
                out.append((name, batch))
        return out

    async def xautoclaim(self, name, group, consumer, min_idle_time, start_id, count):
        claimed = []
        for (stream, msg_id), meta in list(self.pending.items()):
            if stream != name or meta.get("idle_ms", 0) < min_idle_time:
                continue
            meta["consumer"] = consumer
            meta["times_delivered"] = int(meta["times_delivered"]) + 1
            fields = dict(self.streams[name])[msg_id]
            claimed.append((msg_id, fields))
            if len(claimed) >= count:
                break
        return ("0-0", claimed, [])

    async def xack(self, name, group, msg_id):
        self.pending.pop((name, msg_id), None)
        return 1

    async def xpending_range(self, name, group, min, max, count):
        meta = self.pending.get((name, min))
        if meta is None:
            return []
        return [(min, meta["consumer"], 0, meta["times_delivered"])]

    def expire_pending(self, stream: str, msg_id: str) -> None:
        self.pending[(stream, msg_id)]["idle_ms"] = 10 * 60 * 1000


def test_every_contract_stream_has_a_bound_and_an_allowlist():
    assert set(STREAM_MAXLEN) == set(STREAM_PRODUCERS)
    assert all(maxlen > 0 for maxlen in STREAM_MAXLEN.values())
    assert STREAM_MAXLEN["atlas:market.tick"] == 100_000
    assert STREAM_MAXLEN["atlas:mode.changed"] == 500


async def test_publish_refuses_an_unregistered_stream():
    bus = EventBus(FakeRedis())
    envelope = EventEnvelope.create("x", "ingest-market")
    with pytest.raises(StreamNotRegistered):
        await bus.publish("atlas:not.a.stream", envelope)


async def test_publish_refuses_a_producer_outside_the_allowlist():
    bus = EventBus(FakeRedis())
    envelope = EventEnvelope.create("risk.verdict", "agents")
    with pytest.raises(ProducerNotAllowed):
        await bus.publish("atlas:risk.verdict", envelope)


async def test_publish_sets_maxlen_from_the_contract_table():
    redis = FakeRedis()
    bus = EventBus(redis)
    envelope = EventEnvelope.create("market.tick", "ingest-market")
    await bus.publish("atlas:market.tick", envelope)
    assert redis.maxlen_calls == [("atlas:market.tick", 100_000)]


async def test_envelope_round_trips_through_redis_fields():
    envelope = EventEnvelope.create(
        "order.update",
        "execution",
        {"order_id": "abc", "status": "FILLED"},
        venue_id="indodax",
        causation_id="parent-1",
    )
    bus = EventBus(FakeRedis())
    await bus.publish("atlas:order.update", envelope)
    redis = bus._redis
    decoded = await bus.read("atlas:order.update")
    assert len(decoded) == 1
    assert decoded[0].envelope is not None
    assert decoded[0].envelope == envelope
    assert redis.pending


async def test_redelivery_is_acked_but_not_reapplied():
    bus = EventBus(FakeRedis())
    envelope = EventEnvelope.create("market.tick", "ingest-market", {"pair": "btcidr"})
    await bus.publish("atlas:market.tick", envelope)
    first = await bus.read("atlas:market.tick")
    deduper = EventDeduper()
    applied = 0

    async def handler(_envelope: EventEnvelope) -> None:
        nonlocal applied
        applied += 1

    assert await bus.deliver(first[0], handler, deduper=deduper) == "acked"
    # The same event is delivered again after a consumer crash: same event_id, new msg id.
    await bus.publish("atlas:market.tick", envelope)
    second = await bus.read("atlas:market.tick")
    assert second, "the fake must have redelivered something"
    for delivery in second:
        assert await bus.deliver(delivery, handler, deduper=deduper) == "duplicate"
    assert applied == 1
    assert not bus._redis.pending


async def test_stale_pending_messages_are_claimed_after_five_minutes():
    redis = FakeRedis()
    bus = EventBus(redis)
    await bus.publish_event(
        "atlas:market.tick", "market.tick", {"pair": "btcidr"}, producer="ingest-market"
    )
    await bus.read("atlas:market.tick")
    msg_id = next(iter(redis.pending))[1]

    assert await bus.claim_stale("atlas:market.tick") == []
    redis.expire_pending("atlas:market.tick", msg_id)
    claimed = await bus.claim_stale("atlas:market.tick")
    assert len(claimed) == 1
    assert claimed[0].msg_id == msg_id
    assert claimed[0].envelope is not None


async def test_failure_three_times_dead_letters_the_message_and_acks_it():
    redis = FakeRedis()
    bus = EventBus(redis)
    await bus.publish_event("atlas:order.update", "order.update", {"x": 1}, producer="execution")
    delivery = (await bus.read("atlas:order.update"))[0]

    async def always_fails(_envelope: EventEnvelope) -> None:
        raise RuntimeError("handler exploded")

    # Redis counts the initial delivery; two retries follow, the third failure dead-letters.
    assert await bus.deliver(delivery, always_fails) == "retry"
    meta = redis.pending[("atlas:order.update", delivery.msg_id)]
    meta["times_delivered"] = MAX_DELIVERIES
    assert await bus.deliver(delivery, always_fails) == "deadlettered"
    assert ("atlas:order.update", delivery.msg_id) not in redis.pending
    dead = redis.streams[DEADLETTER_STREAM]
    assert len(dead) == 1
    assert dead[0][1]["error"].startswith("RuntimeError")
    assert dead[0][1]["original_stream"] == "atlas:order.update"


async def test_a_malformed_envelope_is_dead_lettered_without_retries():
    redis = FakeRedis()
    bus = EventBus(redis)
    await redis.xadd("atlas:market.bar", {"event_id": "not-a-uuid", "type": "market.bar"})
    delivery = (await bus.read("atlas:market.bar"))[0]
    assert delivery.envelope is None
    assert delivery.decode_error

    async def never_called(_envelope: EventEnvelope) -> None:  # pragma: no cover
        raise AssertionError("a poisoned event must not reach the handler")

    assert await bus.deliver(delivery, never_called) == "deadlettered"
    assert redis.streams[DEADLETTER_STREAM]


async def test_deduper_expires_old_ids_so_memory_is_bounded():
    now = 0.0
    deduper = EventDeduper(ttl_s=10, clock=lambda: now)
    assert deduper.first_seen("a") is True
    assert deduper.first_seen("a") is False
    now = 11.0
    assert deduper.first_seen("a") is True
