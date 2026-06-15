"""DARK_VESSEL — the flagship detector.

Two firings, two severities:

1. GOING DARK (on_tick): a vessel last seen moving (sog > 0.5) inside a zone
   *core* (NOT near the bbox edge — edge silence just means "left coverage")
   has now been silent longer than the zone's gap threshold. Fires once per dark
   episode.

2. REAPPEARANCE (on_update): a previously-dark vessel reports again. We compute
   the gap minutes and the great-circle displacement during the blackout. A
   large jump while dark is the sanctions-evasion / STS signature, so this fires
   a SECOND, higher-severity Signal carrying that evidence.

State is per-MMSI: last_seen_ts, last position, and whether we've already raised
a "dark" Signal for the current episode (so on_tick doesn't spam).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from trident_contracts import Signal, SignalType
from trident_geo import CHOKEPOINTS_BY_ID, near_edge

from ..geoutil import haversine_nm
from .base import DETECTOR_VERSION, Detector, DetectorContext
from .config import (
    DARK_MIN_SOG_KN,
    REAPPEAR_MIN_DISPLACEMENT_NM,
    REAPPEAR_MIN_GAP_MIN,
    gap_threshold_for,
)


@dataclass
class _Last:
    ts: float
    lat: float
    lon: float
    sog: float
    zone: Optional[str]
    dark_raised: bool = False     # already emitted "going dark" this episode?


class DarkVesselDetector(Detector):
    name = "dark_vessel"
    version = DETECTOR_VERSION

    def __init__(self) -> None:
        self._last: dict[int, _Last] = {}

    # -- reappearance --------------------------------------------------------
    async def on_update(self, ctx: DetectorContext, mmsi: int) -> list[Signal]:
        st = ctx.state.get_state(mmsi)
        if st is None:
            return []
        signals: list[Signal] = []
        prev = self._last.get(mmsi)

        if prev is not None and prev.dark_raised:
            # This vessel was dark and just came back.
            gap_min = (st.last_fix_ts - prev.ts) / 60.0
            disp_nm = haversine_nm(prev.lat, prev.lon, st.lat, st.lon)
            if gap_min >= REAPPEAR_MIN_GAP_MIN and disp_nm >= REAPPEAR_MIN_DISPLACEMENT_NM:
                # Severity scales with how far it jumped during the blackout.
                severity = min(1.0, 0.7 + disp_nm / 100.0)
                signals.append(
                    Signal(
                        ts=ctx.now,
                        type=SignalType.DARK_VESSEL,
                        mmsi=mmsi,
                        zone=st.zone or prev.zone or "unknown",
                        severity=severity,
                        confidence=0.85,
                        position=(st.lat, st.lon),
                        detector_version=self.version,
                        evidence={
                            "phase": "reappearance",
                            "gap_minutes": round(gap_min, 1),
                            "displacement_nm": round(disp_nm, 2),
                            "dark_from": (prev.lat, prev.lon),
                            "reappeared_at": (st.lat, st.lon),
                            "indicator": "possible_sts_or_sanctions_evasion",
                            "flag": st.flag,
                        },
                    )
                )

        # Refresh last-seen for this MMSI; new episode resets dark_raised.
        self._last[mmsi] = _Last(
            ts=st.last_fix_ts, lat=st.lat, lon=st.lon, sog=st.sog, zone=st.zone,
            dark_raised=False,
        )
        return signals

    # -- going dark ----------------------------------------------------------
    async def on_tick(self, ctx: DetectorContext) -> list[Signal]:
        signals: list[Signal] = []
        for mmsi, last in self._last.items():
            if last.dark_raised:
                continue
            if last.zone is None:
                continue
            cp = CHOKEPOINTS_BY_ID.get(last.zone)
            if cp is None:
                continue
            silent_for = ctx.now - last.ts
            if silent_for < gap_threshold_for(last.zone):
                continue
            # Must have been MOVING and inside the core (not just leaving coverage).
            if last.sog <= DARK_MIN_SOG_KN:
                continue
            if near_edge(last.lat, last.lon, cp):
                continue

            last.dark_raised = True
            silent_min = silent_for / 60.0
            signals.append(
                Signal(
                    ts=ctx.now,
                    type=SignalType.DARK_VESSEL,
                    mmsi=mmsi,
                    zone=last.zone,
                    severity=0.7,
                    confidence=0.8,
                    position=(last.lat, last.lon),
                    detector_version=self.version,
                    evidence={
                        "phase": "went_dark",
                        "silent_minutes": round(silent_min, 1),
                        "gap_threshold_min": round(gap_threshold_for(last.zone) / 60.0, 1),
                        "last_sog": last.sog,
                        "last_position": (last.lat, last.lon),
                        "in_core": True,
                    },
                )
            )
        return signals
