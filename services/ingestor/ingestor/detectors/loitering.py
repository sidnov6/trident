"""LOITERING — a vessel hanging in one spot outside a designated anchorage.

Over a 60-minute sliding window of the ring buffer: if the max pairwise
displacement stays under 2nm AND the mean speed is under 1.5kn, the vessel is
loitering. Loitering inside a designated anchorage is a benign convoy queue, so
those positions are suppressed via point-in-polygon against suez_anchorage.

This is also half the STS signature: a tanker loitering next to a dark vessel.
"""
from __future__ import annotations

from trident_contracts import Signal, SignalType
from trident_geo import load_zone_geojson

from ..geoutil import haversine_nm, point_in_geojson
from .base import DETECTOR_VERSION, Detector, DetectorContext
from .config import (
    LOITER_MAX_DISPLACEMENT_NM,
    LOITER_MAX_MEAN_SOG_KN,
    LOITER_MIN_FIXES,
    LOITER_WINDOW_S,
)


class LoiteringDetector(Detector):
    name = "loitering"
    version = DETECTOR_VERSION

    def __init__(self) -> None:
        # Lazy-loaded anchorage geofence (benign-loiter mask).
        self._anchorage = load_zone_geojson("suez_anchorage.geojson")
        self._raised: set[int] = set()   # suppress duplicate fires per episode

    async def on_update(self, ctx: DetectorContext, mmsi: int) -> list[Signal]:
        track = ctx.state.get_track(mmsi)
        if len(track) < LOITER_MIN_FIXES:
            return []

        # Window = fixes within the last LOITER_WINDOW_S of the newest fix.
        newest_ts = track[-1][0]
        window = [p for p in track if newest_ts - p[0] <= LOITER_WINDOW_S]
        if len(window) < LOITER_MIN_FIXES:
            return []
        # Need the window to actually span most of the period, else it's just a
        # vessel that only recently appeared.
        if newest_ts - window[0][0] < LOITER_WINDOW_S * 0.5:
            self._raised.discard(mmsi)
            return []

        # Max pairwise displacement (bounding-box diagonal is a cheap proxy, but
        # we compute true max over the window — it's small).
        max_disp = 0.0
        for i in range(len(window)):
            for j in range(i + 1, len(window)):
                d = haversine_nm(window[i][1], window[i][2], window[j][1], window[j][2])
                if d > max_disp:
                    max_disp = d
        if max_disp >= LOITER_MAX_DISPLACEMENT_NM:
            self._raised.discard(mmsi)
            return []

        st = ctx.state.get_state(mmsi)
        if st is None:
            return []
        # Ring buffer holds (ts, lat, lon) — speed comes from the live state plus
        # the geometric speed implied by the window displacement.
        span_h = (window[-1][0] - window[0][0]) / 3600.0
        avg_speed = (max_disp / span_h) if span_h > 0 else 0.0
        # Blend the AIS-reported sog with the geometric speed for robustness.
        mean_sog = (st.sog + avg_speed) / 2.0
        if mean_sog >= LOITER_MAX_MEAN_SOG_KN:
            self._raised.discard(mmsi)
            return []

        # Benign if loitering inside a designated anchorage.
        if point_in_geojson(st.lat, st.lon, self._anchorage):
            return []

        if mmsi in self._raised:
            return []
        self._raised.add(mmsi)

        return [
            Signal(
                ts=ctx.now,
                type=SignalType.LOITERING,
                mmsi=mmsi,
                zone=st.zone or "unknown",
                severity=0.55,
                confidence=0.75,
                position=(st.lat, st.lon),
                detector_version=self.version,
                evidence={
                    "window_minutes": round((window[-1][0] - window[0][0]) / 60.0, 1),
                    "max_displacement_nm": round(max_disp, 2),
                    "mean_sog_kn": round(mean_sog, 2),
                    "fixes": len(window),
                    "in_anchorage": False,
                },
            )
        ]
