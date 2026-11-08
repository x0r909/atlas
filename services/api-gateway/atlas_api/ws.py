"""Websocket fan-out.

The rule the contract cares about is backpressure: a slow browser must never make the
gateway grow without bound, and it must lose the cheapest data first. `market.tick` is
coalesced per pair (only the newest tick survives), everything else is queued once and
the client is closed if it still cannot keep up.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect

TOPICS = ("market", "positions", "orders", "cycles", "health", "incidents")
EVENT_TOPICS = {
    "market.tick": "market",
    "market.bar": "market",
    "market.health": "health",
    "position.update": "positions",
    "order.update": "orders",
    "cycle.started": "cycles",
    "cycle.finished": "cycles",
    "proposal.created": "cycles",
    "risk.verdict": "cycles",
    "mode.changed": "health",
    "incident": "incidents",
}
HEARTBEAT_SECONDS = 15.0
MAX_QUEUE = 500
CLOSE_SLOW_CLIENT = 1013


@dataclass(eq=False)
class Subscriber:
    topics: frozenset[str]
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(MAX_QUEUE))
    ticks: OrderedDict[str, dict[str, Any]] = field(default_factory=OrderedDict)
    heartbeat_seconds: float = HEARTBEAT_SECONDS
    dropped_ticks: int = 0
    closed: bool = False

    def accepts(self, event_type: str) -> bool:
        return EVENT_TOPICS.get(event_type) in self.topics

    def offer(self, event_type: str, payload: dict[str, Any]) -> None:
        """Never blocks. Ticks coalesce; anything else either queues or closes the client."""
        if self.closed:
            return
        frame = {"type": event_type, "payload": payload}
        if event_type == "market.tick":
            pair = str(payload.get("pair", ""))
            if pair in self.ticks:
                self.dropped_ticks += 1
            self.ticks[pair] = frame
            self.ticks.move_to_end(pair)
            while len(self.ticks) > MAX_QUEUE:
                self.ticks.popitem(last=False)
                self.dropped_ticks += 1
            return
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            self.closed = True

    def _take_tick(self) -> dict[str, Any] | None:
        if not self.ticks:
            return None
        _, frame = self.ticks.popitem(last=False)
        return frame

    async def next_frame(self) -> dict[str, Any] | None:
        tick = self._take_tick()
        if tick is not None:
            return tick
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=self.heartbeat_seconds)
        except TimeoutError:
            return None


def parse_subscribe(message: Mapping[str, Any]) -> frozenset[str]:
    topics = message.get("topics", [])
    if not isinstance(topics, list):
        raise ValueError("topics must be a list")
    requested = frozenset(str(topic) for topic in topics)
    unknown = requested - set(TOPICS)
    if unknown:
        raise ValueError(f"unknown topics: {sorted(unknown)}")
    if not requested:
        raise ValueError("subscribe requires at least one topic")
    return requested


async def serve_connection(
    websocket: WebSocket,
    *,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    register: Callable[[Subscriber], None] | None = None,
    unregister: Callable[[Subscriber], None] | None = None,
) -> None:
    await websocket.accept()
    raw = await websocket.receive_json()
    if raw.get("action") != "subscribe":
        await websocket.close(code=1008, reason="first message must be subscribe")
        return
    try:
        subscriber = Subscriber(topics=parse_subscribe(raw), heartbeat_seconds=heartbeat_seconds)
    except ValueError as exc:
        await websocket.close(code=1008, reason=str(exc))
        return
    if register:
        register(subscriber)

    async def pump() -> None:
        while not subscriber.closed:
            frame = await subscriber.next_frame()
            if frame is None:
                await websocket.send_json({"type": "heartbeat", "payload": {}})
                continue
            await websocket.send_json(frame)
        await websocket.close(code=CLOSE_SLOW_CLIENT, reason="client too slow")

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            incoming = await websocket.receive_text()
            if incoming == "ping":
                await websocket.send_json({"type": "heartbeat", "payload": {}})
    except WebSocketDisconnect:
        pass
    finally:
        if unregister:
            unregister(subscriber)
        with contextlib.suppress(asyncio.CancelledError):
            pump_task.cancel()
            await pump_task


class Hub:
    """Holds live subscribers and fans an event out to everyone who asked for its topic."""

    def __init__(self) -> None:
        self._subscribers: set[Subscriber] = set()

    def register(self, subscriber: Subscriber) -> None:
        self._subscribers.add(subscriber)

    def unregister(self, subscriber: Subscriber) -> None:
        self._subscribers.discard(subscriber)

    @property
    def size(self) -> int:
        return len(self._subscribers)

    def publish(self, event_type: str, payload: dict[str, Any]) -> int:
        delivered = 0
        for subscriber in tuple(self._subscribers):
            # A client dropped for backpressure closes itself; keeping it in the set would
            # grow the fan-out forever and count a delivery nobody received.
            if subscriber.closed:
                self._subscribers.discard(subscriber)
                continue
            if subscriber.accepts(event_type):
                subscriber.offer(event_type, payload)
                delivered += 1
        return delivered

    def publish_envelope(self, envelope: Any) -> int:
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        return self.publish(envelope.event_type, payload)


def heartbeat_frame() -> str:
    return json.dumps({"type": "heartbeat", "payload": {}})


def attach(app: Any) -> Hub:
    hub = Hub()
    app.state.hub = hub

    @app.websocket("/ws")
    async def _ws(websocket: WebSocket) -> None:
        await serve_connection(websocket, register=hub.register, unregister=hub.unregister)

    return hub
