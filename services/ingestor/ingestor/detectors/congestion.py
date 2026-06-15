"""CONGESTION — zone-level density anomaly.

Live vessel count per zone (from the Redis GEO index) is tracked against an EWMA
baseline persisted in Redis. When the current count sits more than z=3 standard
deviations above the baseline, the zone is congesting (a blockage forming, the
Ever-Given queue building). This is a zone-level Signal — mmsi is 0.

Baseline state in Redis (`chokepoint:{zone}:baseline` hash): mean, var, n.
"""
from __future__ import annotations

import math

from trident_common import keys
from trident_contracts import Signal, SignalType
from trident_geo import CHOKEPOINTS_BY_ID

from .base import DETECTOR_VERSION, Detector, DetectorContext
from .config import CONGESTION_Z, EWMA_ALPHA, EWMA_MIN_SAMPLES, EWMA_MIN_VAR


class CongestionDetector(Detector):
    name = "congestion"
    version = DETECTOR_VERSION

    def __init__(self) -> None:
        # In-process baseline fallback when Redis is absent (mean, var, n).
        self._local: dict[str, tuple[float, float, int]] = {}
        self._raised: set[str] = set()

    async def on_tick(self, ctx: DetectorContext) -> list[Signal]:
        signals: list[Signal] = []
        for zone, cp in CHOKEPOINTS_BY_ID.items():
            count = await ctx.state.zone_count(zone)
            mean, var, n = await self._load_baseline(ctx, zone)

            z = 0.0
            if n >= EWMA_MIN_SAMPLES:
                std = math.sqrt(max(var, EWMA_MIN_VAR))
                z = (count - mean) / std

            if n >= EWMA_MIN_SAMPLES and z > CONGESTION_Z:
                if zone not in self._raised:
                    self._raised.add(zone)
                    signals.append(
                        Signal(
                            ts=ctx.now,
                            type=SignalType.CONGESTION,
                            mmsi=0,                         # zone-level
                            zone=zone,
                            severity=min(1.0, 0.5 + (z - CONGESTION_Z) / 10.0),
                            confidence=0.7,
                            position=cp.center,
                            detector_version=self.version,
                            evidence={
                                "count": count,
                                "baseline_mean": round(mean, 2),
                                "z_score": round(z, 2),
                                "threshold_z": CONGESTION_Z,
                                "samples": n,
                            },
                        )
                    )
            elif z < CONGESTION_Z * 0.6:
                self._raised.discard(zone)   # re-arm once it relaxes

            # EWMA update AFTER scoring, so a spike doesn't poison its own z.
            mean2 = (1 - EWMA_ALPHA) * mean + EWMA_ALPHA * count if n else count
            var2 = (
                (1 - EWMA_ALPHA) * var + EWMA_ALPHA * (count - mean2) ** 2
                if n else 0.0
            )
            await self._save_baseline(ctx, zone, mean2, var2, n + 1, count)
        return signals

    # -- baseline persistence ---------------------------------------------
    async def _load_baseline(self, ctx, zone):
        if ctx.redis is None:
            return self._local.get(zone, (0.0, 0.0, 0))
        try:
            h = await ctx.redis.hgetall(keys.zone_baseline_key(zone))
            if not h:
                return (0.0, 0.0, 0)
            g = lambda k: h.get(k) or h.get(k.encode())
            return (float(g("mean") or 0), float(g("var") or 0), int(g("n") or 0))
        except Exception:
            return self._local.get(zone, (0.0, 0.0, 0))

    async def _save_baseline(self, ctx, zone, mean, var, n, count):
        self._local[zone] = (mean, var, n)
        if ctx.redis is None:
            return
        try:
            await ctx.redis.hset(
                keys.zone_baseline_key(zone),
                mapping={"mean": mean, "var": var, "n": n},
            )
            await ctx.redis.set(keys.zone_count_key(zone), count)
        except Exception:
            pass
