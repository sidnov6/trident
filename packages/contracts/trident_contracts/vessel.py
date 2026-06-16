"""Canonical vessel models.

`VesselState` is the rich in-memory / Redis-backed world model record.
`VesselLite` is the tiny wire form pushed to the UI on the hot path.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class VesselState(BaseModel):
    """The merged dynamic + static record keyed by MMSI.

    Held in Redis (HSET vessel:{mmsi}) and mirrored in-process with a track
    ring buffer the detectors read.
    """

    mmsi: int
    lat: float
    lon: float
    sog: float = 0.0          # speed over ground, knots
    cog: float = 0.0          # course over ground, degrees
    heading: Optional[float] = None
    nav_status: int = 15      # 15 = "not defined" (AIS default)

    # static (from ShipStaticData)
    name: Optional[str] = None
    imo: Optional[int] = None
    ship_type: Optional[int] = None        # raw AIS code; 80-89 tanker, 70-79 cargo
    destination: Optional[str] = None
    draught: Optional[float] = None
    length: Optional[float] = None
    beam: Optional[float] = None
    flag: Optional[str] = None             # derived from MMSI MID prefix

    last_fix_ts: float = 0.0   # epoch seconds — critical for dead-reckoning
    first_seen_ts: float = 0.0
    zone: Optional[str] = None             # current chokepoint id, if any


class VesselLite(BaseModel):
    """Minimal vessel delta on the WebSocket hot path. Field names are terse on
    purpose — this is serialised hundreds of times per second."""

    m: int                      # mmsi
    la: float                   # latitude
    lo: float                   # longitude
    s: float = 0.0              # sog (knots) — needed for dead-reckoning
    c: float = 0.0              # cog (degrees)
    t: int = 0                  # ship_type bucket (ShipTypeBucket value) -> colour
    f: float = 0.0              # last_fix_ts (epoch s) -> drives interpolation
    st: int = 0                 # status bitfield (STATUS_BIT_*)

    @classmethod
    def from_state(cls, v: "VesselState", bucket: int, status_bits: int = 0) -> "VesselLite":
        return cls(
            m=v.mmsi, la=v.lat, lo=v.lon, s=v.sog, c=v.cog,
            t=bucket, f=v.last_fix_ts, st=status_bits,
        )


class VesselDossier(BaseModel):
    """Full identity + history record served to the dossier slide-over panel."""

    mmsi: int
    imo: Optional[int] = None
    name: Optional[str] = None
    flag: Optional[str] = None
    ship_type: Optional[int] = None
    destination: Optional[str] = None
    draught: Optional[float] = None
    length: Optional[float] = None
    beam: Optional[float] = None
    first_seen_ts: Optional[float] = None
    last_fix_ts: Optional[float] = None
    track: list[tuple[float, float, float]] = Field(default_factory=list)  # (ts, lat, lon)
    incident_ids: list[str] = Field(default_factory=list)

    # live kinematics + investigation enrichment (Phase 2)
    lat: Optional[float] = None
    lon: Optional[float] = None
    sog: Optional[float] = None
    cog: Optional[float] = None
    heading: Optional[float] = None
    course_compass: Optional[str] = None       # e.g. "SSW" — where it's heading
    origin: Optional[tuple[float, float, float]] = None  # (ts, lat, lon) first known fix
    flag_of_convenience: bool = False
    on_watchlist: bool = False
    watch_category: Optional[str] = None        # ThreatCategory if flagged
    watch_reason: Optional[str] = None
