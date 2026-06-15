"""The Signal contract — the typed event a detector emits onto the bus.

A Signal is the *only* thing that crosses from the deterministic fast lane into
the cognition slow lane. It must carry enough evidence to be auditable on its own.
"""
from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from .enums import SignalType


def _new_id() -> str:
    return str(uuid.uuid4())


class Signal(BaseModel):
    id: str = Field(default_factory=_new_id)
    ts: float                              # detection time (epoch seconds)
    type: SignalType
    mmsi: int
    zone: str                              # which chokepoint
    severity: float = Field(ge=0.0, le=1.0)   # how alarming if real
    confidence: float = Field(ge=0.0, le=1.0)  # how sure the detector is
    position: tuple[float, float]          # (lat, lon)
    evidence: dict[str, Any] = Field(default_factory=dict)  # detector-specific facts
    detector_version: str = "0.0.0"        # for audit reproducibility

    # Redis Streams transport helpers --------------------------------------
    def to_stream_fields(self) -> dict[str, str]:
        """Flatten to the string map Redis XADD wants."""
        return {"payload": self.model_dump_json()}

    @classmethod
    def from_stream_fields(cls, fields: dict[str, Any]) -> "Signal":
        raw = fields.get("payload") or fields.get(b"payload")
        if isinstance(raw, bytes):
            raw = raw.decode()
        return cls.model_validate_json(raw)


class SignalLite(BaseModel):
    """Compact form for the UI activity ticker."""

    id: str
    ts: float
    type: SignalType
    mmsi: int
    zone: str
    severity: float

    @classmethod
    def from_signal(cls, s: Signal) -> "SignalLite":
        return cls(id=s.id, ts=s.ts, type=s.type, mmsi=s.mmsi, zone=s.zone, severity=s.severity)
