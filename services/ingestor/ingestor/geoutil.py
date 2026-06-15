"""Pure geometric helpers used across the detector suite.

No external geo dependency (no shapely): great-circle distance and ray-casting
point-in-polygon are implemented here in plain Python so the per-message path
stays cheap and the detectors stay trivially testable.

Convention reminder: TRIDENT positions are (lat, lon) tuples everywhere in the
contract. GeoJSON polygons, however, store coordinates as [lon, lat]. Helpers
that consume GeoJSON note the order explicitly.
"""
from __future__ import annotations

import math

EARTH_RADIUS_NM = 3440.065  # mean Earth radius in nautical miles


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two (lat, lon) points, in nautical miles."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = rlat2 - rlat1
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_NM * math.asin(min(1.0, math.sqrt(a)))


def implied_speed_kn(
    lat1: float, lon1: float, ts1: float, lat2: float, lon2: float, ts2: float
) -> float:
    """Speed (knots) implied by the displacement between two timed fixes.

    Returns 0.0 if the timestamps are equal or out of order (caller treats a
    same-timestamp pair as a potential identity conflict, not a speed event)."""
    dt_h = (ts2 - ts1) / 3600.0
    if dt_h <= 0:
        return 0.0
    return haversine_nm(lat1, lon1, lat2, lon2) / dt_h


def angular_diff(a: float, b: float) -> float:
    """Smallest absolute difference between two compass bearings, in [0, 180]."""
    d = abs((a - b) % 360.0)
    return d if d <= 180.0 else 360.0 - d


def point_in_ring(lat: float, lon: float, ring: list) -> bool:
    """Ray-casting point-in-polygon for a single GeoJSON ring.

    `ring` is a list of [lon, lat] vertices (GeoJSON order). The point is given
    as (lat, lon) to match the rest of TRIDENT."""
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]   # lon_i, lat_i
        xj, yj = ring[j][0], ring[j][1]
        # Does the horizontal ray at `lat` cross edge (j -> i)?
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def point_in_geojson(lat: float, lon: float, feature_collection: dict) -> bool:
    """True if (lat, lon) falls inside ANY Polygon feature in a GeoJSON
    FeatureCollection. Outer ring only (these zones have no holes); honouring a
    hole would just flip membership back, which none of our zones need."""
    for feat in feature_collection.get("features", []):
        geom = feat.get("geometry", {})
        if geom.get("type") != "Polygon":
            continue
        rings = geom.get("coordinates", [])
        if rings and point_in_ring(lat, lon, rings[0]):
            return True
    return False
