"""Per-MMSI cross-sweep memory + the parsed vessel snapshot.

Memory is in-process and bounded — pruned when an MMSI drops out of the live
GLOBAL_GEO index (same shape as the ingestor's dark_vessel detector state). No
Redis writes on the sweep hot path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from trident_contracts.enums import bucket_for_ship_type
from trident_geo import flag_for_mmsi, is_flag_of_convenience


@dataclass
class Snapshot:
    """A parsed vessel:{mmsi} Redis hash — only the fields the agents use."""

    mmsi: int
    lat: float
    lon: float
    sog: float
    cog: float
    heading: Optional[float]
    nav_status: int
    name: Optional[str]
    imo: Optional[int]
    ship_type: Optional[int]
    destination: Optional[str]
    draught: Optional[float]
    flag: Optional[str]
    first_seen_ts: float
    last_fix_ts: float
    zone: Optional[str]

    @property
    def bucket(self) -> int:
        return int(bucket_for_ship_type(self.ship_type))

    @property
    def is_foc(self) -> bool:
        return is_flag_of_convenience(self.flag)


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default=None):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def parse_hash(mmsi: int, h: dict) -> Optional[Snapshot]:
    """Build a Snapshot from a Redis hash (string map). None if unusable."""
    if not h:
        return None
    g = {}
    for k, v in h.items():
        if isinstance(k, bytes):
            k = k.decode()
        if isinstance(v, bytes):
            v = v.decode()
        g[k] = v
    if "lat" not in g or "lon" not in g:
        return None
    flag = g.get("flag") or flag_for_mmsi(mmsi)
    return Snapshot(
        mmsi=mmsi,
        lat=_f(g.get("lat")), lon=_f(g.get("lon")),
        sog=_f(g.get("sog")), cog=_f(g.get("cog")),
        heading=_i(g.get("heading")),
        nav_status=_i(g.get("nav_status"), 15) or 15,
        name=(g.get("name") or None),
        imo=_i(g.get("imo")),
        ship_type=_i(g.get("ship_type")),
        destination=(g.get("destination") or None),
        draught=_f(g.get("draught")) or None,
        flag=flag,
        first_seen_ts=_f(g.get("first_seen_ts")),
        last_fix_ts=_f(g.get("last_fix_ts")),
        zone=(g.get("zone") or None),
    )


@dataclass
class AgentMemory:
    """What the agents remember about a vessel between sweeps."""

    prev_lat: Optional[float] = None
    prev_lon: Optional[float] = None
    prev_ts: Optional[float] = None
    loiter_streak: int = 0
    was_dark: bool = False
    dark_lat: Optional[float] = None
    dark_lon: Optional[float] = None
    last_seen_sweep: float = 0.0
    last_published: dict = field(default_factory=dict)  # category -> severity
