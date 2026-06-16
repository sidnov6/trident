"""Great-circle helpers (vendored from ingestor/geoutil — no external deps)."""
from __future__ import annotations

import math

EARTH_NM = 3440.065  # earth radius in nautical miles


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_NM * math.asin(min(1.0, math.sqrt(a)))


def implied_speed_kn(lat1: float, lon1: float, t1: float,
                     lat2: float, lon2: float, t2: float) -> float:
    dt_h = abs(t2 - t1) / 3600.0
    if dt_h <= 0:
        return 0.0
    return haversine_nm(lat1, lon1, lat2, lon2) / dt_h


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
