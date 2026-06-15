"""Spoofing detectors — POSITION_JUMP and IDENTITY_CONFLICT.

POSITION_JUMP: implied speed between two consecutive fixes exceeds the maximum
plausible vessel speed (~40kn). The vessel "teleported" — a GPS spoof or a
manufactured track.

IDENTITY_CONFLICT: two fixes for the same MMSI carry the SAME timestamp but
different positions — two physical emitters sharing one identity (cloning).
"""
from __future__ import annotations

from dataclasses import dataclass

from trident_contracts import Signal, SignalType

from ..geoutil import haversine_nm, implied_speed_kn
from .base import DETECTOR_VERSION, Detector, DetectorContext
from .config import IDENTITY_CONFLICT_MIN_NM, MAX_PLAUSIBLE_KN


@dataclass
class _Fix:
    ts: float
    lat: float
    lon: float


class SpoofingDetector(Detector):
    name = "spoofing"
    version = DETECTOR_VERSION

    def __init__(self) -> None:
        self._prev: dict[int, _Fix] = {}

    async def on_update(self, ctx: DetectorContext, mmsi: int) -> list[Signal]:
        st = ctx.state.get_state(mmsi)
        if st is None:
            return []
        cur = _Fix(ts=st.last_fix_ts, lat=st.lat, lon=st.lon)
        prev = self._prev.get(mmsi)
        self._prev[mmsi] = cur
        if prev is None:
            return []

        signals: list[Signal] = []

        if cur.ts == prev.ts:
            # Same timestamp, different position -> identity conflict.
            disp = haversine_nm(prev.lat, prev.lon, cur.lat, cur.lon)
            if disp >= IDENTITY_CONFLICT_MIN_NM:
                signals.append(
                    Signal(
                        ts=ctx.now,
                        type=SignalType.IDENTITY_CONFLICT,
                        mmsi=mmsi,
                        zone=st.zone or "unknown",
                        severity=0.7,
                        confidence=0.8,
                        position=(cur.lat, cur.lon),
                        detector_version=self.version,
                        evidence={
                            "timestamp": cur.ts,
                            "position_a": (prev.lat, prev.lon),
                            "position_b": (cur.lat, cur.lon),
                            "separation_nm": round(disp, 2),
                        },
                    )
                )
            return signals

        implied = implied_speed_kn(prev.lat, prev.lon, prev.ts, cur.lat, cur.lon, cur.ts)
        if implied > MAX_PLAUSIBLE_KN:
            signals.append(
                Signal(
                    ts=ctx.now,
                    type=SignalType.POSITION_JUMP,
                    mmsi=mmsi,
                    zone=st.zone or "unknown",
                    severity=min(1.0, 0.6 + implied / 200.0),
                    confidence=0.8,
                    position=(cur.lat, cur.lon),
                    detector_version=self.version,
                    evidence={
                        "implied_speed_kn": round(implied, 1),
                        "max_plausible_kn": MAX_PLAUSIBLE_KN,
                        "from": (prev.lat, prev.lon),
                        "to": (cur.lat, cur.lon),
                        "dt_s": round(cur.ts - prev.ts, 1),
                    },
                )
            )
        return signals
