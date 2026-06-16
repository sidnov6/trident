"""FleetAlert — the real-time threat the fleetscan agents emit.

This is the contract between the always-on deterministic fleet scanner (producer)
and the API/UI (consumers). It rides a dedicated Redis stream, separate from the
zone-forensic Signal stream, and is denormalised (name/flag/cog/position) so the
ticker renders straight from the alert with no extra lookup.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field

from .enums import ThreatCategory


def _new_id() -> str:
    return str(uuid.uuid4())


class FleetAlert(BaseModel):
    id: str = Field(default_factory=_new_id)
    ts: float                               # detection epoch seconds
    category: ThreatCategory                # which agent fired (danger category)
    agent: str                              # agent name (audit)
    mmsi: int
    name: Optional[str] = None              # denormalised for the ticker
    flag: Optional[str] = None
    ship_bucket: int = 0                     # ShipTypeBucket -> colour/icon
    severity: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    risk: float = Field(default=0.0, ge=0.0, le=1.0)   # composite per-vessel risk
    position: tuple[float, float]           # (lat, lon) — map fly-to target
    cog: float = 0.0                        # current direction ("where it's going")
    sog: float = 0.0
    zone: Optional[str] = None              # chokepoint id if any, else None
    evidence: list[str] = Field(default_factory=list)  # plain-language facts
    narrative: Optional[str] = None         # LLM, filled for top-N only
    detector_version: str = "fleet-0.0.0"

    # --- Redis Streams transport (same shape as Signal) -------------------
    def to_stream_fields(self) -> dict[str, str]:
        return {"payload": self.model_dump_json()}

    @classmethod
    def from_stream_fields(cls, fields: dict[str, Any]) -> "FleetAlert":
        raw = fields.get("payload") or fields.get(b"payload")
        if isinstance(raw, bytes):
            raw = raw.decode()
        return cls.model_validate_json(raw)


class FleetAlertLite(BaseModel):
    """Compact wire form for the high-frequency ticker."""

    id: str
    ts: float
    category: ThreatCategory
    mmsi: int
    name: Optional[str] = None
    severity: float
    risk: float
    position: tuple[float, float]

    @classmethod
    def from_alert(cls, a: FleetAlert) -> "FleetAlertLite":
        return cls(
            id=a.id, ts=a.ts, category=a.category, mmsi=a.mmsi,
            name=a.name, severity=a.severity, risk=a.risk, position=a.position,
        )
