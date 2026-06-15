"""GEOFENCE_BREACH — a vessel intruding into a canal-bank exclusion strip.

Point-in-polygon against suez_exclusion.geojson (west/east bank restricted
strips). A vessel inside one of these is grounding or making bank contact — the
Ever-Given signature. Pure ray-casting, no shapely.

A grounding is a *dwell*, not a clip: a vessel transiting the narrow canal may
momentarily register inside an approximate bank strip while passing through. A
real bank contact / grounding (the Ever-Given signature) STAYS there. We
therefore require the vessel to persist in the strip across DWELL_FIXES
consecutive fixes AND to have barely moved over that span (it is wedged in, not
sailing through). A fast clean transit is suppressed; a grounding fires.
"""
from __future__ import annotations

from trident_contracts import Signal, SignalType
from trident_geo import load_zone_geojson

from ..geoutil import haversine_nm, point_in_geojson
from .base import DETECTOR_VERSION, Detector, DetectorContext

DWELL_FIXES = 3          # consecutive in-strip fixes before a breach is real
DWELL_MAX_DISP_NM = 0.5  # ...and the vessel must be stuck (moved < this) across them


class GeofenceDetector(Detector):
    name = "geofence"
    version = DETECTOR_VERSION

    def __init__(self) -> None:
        self._exclusion = load_zone_geojson("suez_exclusion.geojson")
        self._raised: set[int] = set()   # one fire per breach episode
        # per-mmsi: (dwell_count, entry_lat, entry_lon)
        self._dwell: dict[int, tuple[int, float, float]] = {}

    async def on_update(self, ctx: DetectorContext, mmsi: int) -> list[Signal]:
        st = ctx.state.get_state(mmsi)
        if st is None:
            return []

        inside = point_in_geojson(st.lat, st.lon, self._exclusion)
        if not inside:
            self._dwell.pop(mmsi, None)
            self._raised.discard(mmsi)    # left the strip -> re-arm
            return []

        count, elat, elon = self._dwell.get(mmsi, (0, st.lat, st.lon))
        count += 1
        self._dwell[mmsi] = (count, elat, elon)

        # Not yet a confirmed grounding: too few fixes, or the vessel is moving
        # through the strip (displacement from entry too large).
        if count < DWELL_FIXES:
            return []
        if haversine_nm(elat, elon, st.lat, st.lon) > DWELL_MAX_DISP_NM:
            return []
        if mmsi in self._raised:
            return []
        self._raised.add(mmsi)

        return [
            Signal(
                ts=ctx.now,
                type=SignalType.GEOFENCE_BREACH,
                mmsi=mmsi,
                zone=st.zone or "suez",
                severity=0.8,
                confidence=0.9,
                position=(st.lat, st.lon),
                detector_version=self.version,
                evidence={
                    "geofence": "canal_bank_exclusion",
                    "position": (st.lat, st.lon),
                    "sog": st.sog,
                    "heading": st.heading,
                },
            )
        ]
