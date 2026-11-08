"""Every message on Redis Streams uses this envelope (contracts/event-bus.md)."""

from __future__ import annotations

import json
import secrets
import time
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 1


def new_event_id() -> UUID:
    """A UUIDv7-shaped id: 48-bit epoch ms prefix, so ids sort by production time."""
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = secrets.token_bytes(10)
    raw = bytearray(ms.to_bytes(6, "big") + rand)
    raw[6] = (raw[6] & 0x0F) | 0x70  # version 7
    raw[8] = (raw[8] & 0x3F) | 0x80  # RFC 4122 variant
    return UUID(bytes=bytes(raw))


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    event_type: str
    schema_version: int = SCHEMA_VERSION
    produced_at: int
    producer: str
    correlation_id: str
    causation_id: str | None = None
    venue_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_type", "producer")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @classmethod
    def create(
        cls,
        event_type: str,
        producer: str,
        payload: dict[str, Any] | None = None,
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        venue_id: str | None = None,
    ) -> EventEnvelope:
        event_id = new_event_id()
        return cls(
            event_id=event_id,
            event_type=event_type,
            produced_at=int(time.time() * 1000),
            producer=producer,
            correlation_id=correlation_id or str(event_id),
            causation_id=causation_id,
            venue_id=venue_id,
            payload=payload or {},
        )

    def redis_fields(self) -> dict[str, str]:
        return {
            "event_id": str(self.event_id),
            "type": self.event_type,
            "v": str(self.schema_version),
            "ts": str(self.produced_at),
            "producer": self.producer,
            "corr": self.correlation_id,
            "cause": self.causation_id or "",
            "venue": self.venue_id or "",
            "payload": json.dumps(self.payload, sort_keys=True, separators=(",", ":")),
        }

    @classmethod
    def from_redis_fields(cls, fields: dict[str, str]) -> EventEnvelope:
        try:
            payload = json.loads(fields.get("payload") or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid envelope payload: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("envelope payload must be a JSON object")
        return cls(
            event_id=UUID(fields["event_id"]),
            event_type=fields["type"],
            schema_version=int(fields["v"]),
            produced_at=int(fields["ts"]),
            producer=fields["producer"],
            correlation_id=fields["corr"],
            causation_id=fields.get("cause") or None,
            venue_id=fields.get("venue") or None,
            payload=payload,
        )


__all__ = ["SCHEMA_VERSION", "EventEnvelope", "new_event_id"]
