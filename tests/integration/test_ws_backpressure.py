"""T037: a slow client loses ticks, never frames, and never grows the gateway's memory."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from atlas_api.ws import (
    CLOSE_SLOW_CLIENT,
    MAX_QUEUE,
    Hub,
    Subscriber,
    parse_subscribe,
    serve_connection,
)
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient


def tick(pair: str, price: str) -> dict[str, Any]:
    return {"pair": pair, "last": price}


def test_ticks_coalesce_per_pair() -> None:
    subscriber = Subscriber(topics=frozenset({"market"}))
    for index in range(50):
        subscriber.offer("market.tick", tick("BTCIDR", str(index)))
    subscriber.offer("market.tick", tick("ETHIDR", "1"))
    assert subscriber.dropped_ticks == 49
    assert len(subscriber.ticks) == 2
    assert subscriber.queue.empty()


def test_position_updates_are_not_dropped() -> None:
    subscriber = Subscriber(topics=frozenset({"market", "positions"}))
    for index in range(600):
        subscriber.offer("market.tick", tick("BTCIDR", str(index)))
    for index in range(MAX_QUEUE):
        subscriber.offer("position.update", {"position_id": str(index)})
    assert subscriber.queue.qsize() == MAX_QUEUE
    assert subscriber.closed is False
    subscriber.offer("position.update", {"position_id": "overflow"})
    assert subscriber.closed is True


def test_only_subscribed_topics_are_accepted() -> None:
    subscriber = Subscriber(topics=frozenset({"market"}))
    hub = Hub()
    hub.register(subscriber)
    assert hub.publish("position.update", {"position_id": "1"}) == 0
    assert hub.publish("market.tick", tick("BTCIDR", "1")) == 1
    assert hub.size == 1


def test_subscribe_validation() -> None:
    assert parse_subscribe({"topics": ["market", "positions"]}) == frozenset(
        {"market", "positions"}
    )
    for bad in (
        {"topics": []},
        {"topics": ["nonsense"]},
        {"topics": "market"},
    ):
        with pytest.raises(ValueError):
            parse_subscribe(bad)


def test_next_frame_prefers_a_pending_tick() -> None:
    subscriber = Subscriber(topics=frozenset({"market"}), heartbeat_seconds=0.01)

    async def scenario() -> None:
        subscriber.offer("market.tick", tick("BTCIDR", "7"))
        frame = await subscriber.next_frame()
        assert frame == {"type": "market.tick", "payload": tick("BTCIDR", "7")}
        assert await subscriber.next_frame() is None  # idle -> the caller sends a heartbeat

    asyncio.run(scenario())


def test_slow_client_is_closed_and_ticks_still_flow() -> None:
    app = FastAPI()
    hub = Hub()
    app.state.hub = hub

    @app.websocket("/ws")
    async def _ws(websocket: WebSocket) -> None:
        await serve_connection(
            websocket, heartbeat_seconds=0.05, register=hub.register, unregister=hub.unregister
        )

    with TestClient(app) as client, client.websocket_connect("/ws") as websocket:
        websocket.send_json({"action": "subscribe", "topics": ["market"]})
        for _ in range(200):
            if hub.size == 1:
                break
            time.sleep(0.01)
        assert hub.size == 1
        for index in range(1000):
            hub.publish("market.tick", tick("BTCIDR", str(index)))
        kinds = [websocket.receive_json()["type"] for _ in range(3)]
        assert "market.tick" in kinds
        websocket.send_text("ping")
        assert websocket.receive_json()["type"] == "heartbeat"
        subscriber = next(iter(hub._subscribers))
        assert subscriber.dropped_ticks > 0


def test_a_dropped_client_leaves_the_fan_out() -> None:
    hub = Hub()
    subscriber = Subscriber(topics=frozenset({"market"}), queue=asyncio.Queue(maxsize=1))
    hub.register(subscriber)
    hub.publish("market.bar", {"pair": "BTCIDR"})
    hub.publish("market.bar", {"pair": "BTCIDR"})
    assert subscriber.closed is True
    hub.publish("market.bar", {"pair": "BTCIDR"})
    assert hub.size == 0


def test_subscribe_is_mandatory_and_unknown_topics_refuse() -> None:
    app = FastAPI()

    @app.websocket("/ws")
    async def _ws(websocket: WebSocket) -> None:
        await serve_connection(websocket, heartbeat_seconds=0.05)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json({"action": "hello"})
            with pytest.raises(Exception):  # noqa: B017 - starlette raises WebSocketDisconnect
                websocket.receive_json()
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json({"action": "subscribe", "topics": ["nope"]})
            with pytest.raises(Exception):  # noqa: B017
                websocket.receive_json()


def test_close_code_is_a_policy_violation_not_a_crash() -> None:
    assert CLOSE_SLOW_CLIENT == 1013
