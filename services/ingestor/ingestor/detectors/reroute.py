"""REROUTE / UTURN — course-reversal detection.

Per-vessel (on_update): a sustained COG reversal of more than 150 degrees over
N consecutive fixes is a REROUTE — the vessel turned around (aborted transit,
diverting). We compare the current heading against the heading N fixes ago using
the ring buffer's inter-fix bearings.

Zone-level (on_tick): if several vessels execute U-turns in the same zone inside
a short window, that's a UTURN cluster — the blockage signature where a whole
convoy reverses (the Ever-Given fallout). mmsi is 0 for the cluster Signal.
"""
from __future__ import annotations

import math

from trident_contracts import Signal, SignalType
from trident_geo import CHOKEPOINTS_BY_ID

from ..geoutil import angular_diff
from .base import DETECTOR_VERSION, Detector, DetectorContext
from .config import (
    UTURN_CLUSTER_MIN_COUNT,
    UTURN_CLUSTER_WINDOW_S,
    UTURN_MIN_DEG,
    UTURN_SUSTAIN_FIXES,
)


def _bearing(lat1, lon1, lat2, lon2) -> float:
    """Initial compass bearing from point 1 to point 2, degrees [0, 360)."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(rlat2)
    y = math.cos(rlat1) * math.sin(rlat2) - math.sin(rlat1) * math.cos(rlat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


class RerouteDetector(Detector):
    name = "reroute"
    version = DETECTOR_VERSION

    def __init__(self) -> None:
        self._raised: set[int] = set()
        # Recent per-zone U-turn events: zone -> list[(ts, mmsi)].
        self._uturns: dict[str, list[tuple[float, int]]] = {}
        self._cluster_raised: dict[str, float] = {}   # zone -> ts of last cluster

    async def on_update(self, ctx: DetectorContext, mmsi: int) -> list[Signal]:
        track = ctx.state.get_track(mmsi)
        # Need enough fixes to measure a sustained heading before/after.
        need = UTURN_SUSTAIN_FIXES + 1
        if len(track) < need + 1:
            return []

        # Heading "before" = bearing across the oldest pair in the window;
        # heading "after" = bearing across the newest pair.
        recent = track[-(need + 1):]
        before = _bearing(recent[0][1], recent[0][2], recent[1][1], recent[1][2])
        after = _bearing(recent[-2][1], recent[-2][2], recent[-1][1], recent[-1][2])
        reversal = angular_diff(before, after)

        if reversal < UTURN_MIN_DEG:
            self._raised.discard(mmsi)
            return []
        if mmsi in self._raised:
            return []
        self._raised.add(mmsi)

        st = ctx.state.get_state(mmsi)
        if st is None:
            return []
        zone = st.zone or "unknown"

        # Record for the zone-level cluster aggregation.
        self._uturns.setdefault(zone, []).append((ctx.now, mmsi))

        return [
            Signal(
                ts=ctx.now,
                type=SignalType.REROUTE,
                mmsi=mmsi,
                zone=zone,
                severity=0.5,
                confidence=0.7,
                position=(st.lat, st.lon),
                detector_version=self.version,
                evidence={
                    "heading_before": round(before, 1),
                    "heading_after": round(after, 1),
                    "reversal_deg": round(reversal, 1),
                    "sustain_fixes": UTURN_SUSTAIN_FIXES,
                },
            )
        ]

    async def on_tick(self, ctx: DetectorContext) -> list[Signal]:
        signals: list[Signal] = []
        for zone, events in self._uturns.items():
            # Drop events outside the cluster window.
            fresh = [(t, m) for (t, m) in events if ctx.now - t <= UTURN_CLUSTER_WINDOW_S]
            self._uturns[zone] = fresh
            distinct = {m for (_, m) in fresh}
            if len(distinct) < UTURN_CLUSTER_MIN_COUNT:
                continue
            # Suppress repeat cluster fires within the same window (but allow the
            # first one — a never-raised zone has no entry).
            last = self._cluster_raised.get(zone)
            if last is not None and ctx.now - last < UTURN_CLUSTER_WINDOW_S:
                continue
            self._cluster_raised[zone] = ctx.now

            cp = CHOKEPOINTS_BY_ID.get(zone)
            center = cp.center if cp else (0.0, 0.0)
            signals.append(
                Signal(
                    ts=ctx.now,
                    type=SignalType.UTURN,
                    mmsi=0,                              # zone-level cluster
                    zone=zone,
                    severity=min(1.0, 0.6 + len(distinct) / 20.0),
                    confidence=0.75,
                    position=center,
                    detector_version=self.version,
                    evidence={
                        "uturn_count": len(distinct),
                        "window_min": round(UTURN_CLUSTER_WINDOW_S / 60.0, 1),
                        "mmsis": sorted(distinct),
                        "signature": "convoy_reversal_blockage",
                    },
                )
            )
        return signals
