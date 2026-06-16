"""WebSocket message contracts (API -> UI).

One channel, four message kinds, all batched server-side and applied on the
client's requestAnimationFrame loop.
"""
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field

from .fleet_alert import FleetAlert
from .incident import Incident
from .signal import SignalLite
from .vessel import VesselLite


class VesselDeltaMsg(BaseModel):
    kind: Literal["vessel_delta"] = "vessel_delta"
    vessels: list[VesselLite] = Field(default_factory=list)
    ts: float = 0.0


class SignalTickMsg(BaseModel):
    kind: Literal["signal_tick"] = "signal_tick"
    signal: SignalLite


class IncidentMsg(BaseModel):
    kind: Literal["incident"] = "incident"
    incident: Incident


class FleetAlertMsg(BaseModel):
    kind: Literal["fleet_alert"] = "fleet_alert"
    alert: FleetAlert


class ZoneStatsMsg(BaseModel):
    kind: Literal["zone_stats"] = "zone_stats"
    zone: str
    count: int = 0
    z: float = 0.0                 # congestion z-score
    transit_min: float | None = None
    threat_level: str = "GREEN"    # ThreatLevel value


WSMessage = Union[
    VesselDeltaMsg, SignalTickMsg, IncidentMsg, ZoneStatsMsg, FleetAlertMsg
]
