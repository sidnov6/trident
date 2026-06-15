"""The asyncpg SQL behind the replay service — pure string + param builders.

Kept free of any live-DB or framework dependency so the conversions and SQL
construction are unit-testable on their own (see tests/test_queries.py).

Indexes these queries rely on (defined in services/db/schema.sql):
  * ``tracks_geom_idx``     GIST(geom)         — spatial filter for ST_DWithin.
  * ``tracks_mmsi_ts_idx``  (mmsi, ts DESC)    — per-vessel time-window scans.

A note on distance units
------------------------
``geom`` is ``GEOMETRY(Point, 4326)`` — degrees, not metres. Casting to
``geography`` makes ST_DWithin / ST_Distance compute *true* metres on the
spheroid, which is what an analyst means by "within 0.5 nautical miles". We
therefore cast both operands and pass the radius in metres.
"""
from __future__ import annotations

# 1 international nautical mile = exactly 1852 metres.
METERS_PER_NM: float = 1852.0


def nm_to_meters(nm: float) -> float:
    """Convert nautical miles to metres (the unit geography ST_DWithin wants)."""
    if nm < 0:
        raise ValueError("radius_nm must be non-negative")
    return nm * METERS_PER_NM


# ---------------------------------------------------------------------------
# Track fetch: ordered (ts, lat, lon, sog, cog) for one MMSI in a window.
# Uses tracks_mmsi_ts_idx. Returned ascending so the UI / replay plays forward.
# ---------------------------------------------------------------------------
TRACK_SQL = """
SELECT extract(epoch FROM ts) AS ts,
       ST_Y(geom)             AS lat,
       ST_X(geom)             AS lon,
       sog,
       cog
FROM tracks
WHERE mmsi = $1
  AND ts >= to_timestamp($2)
  AND ts <= to_timestamp($3)
ORDER BY ts ASC
"""


# ---------------------------------------------------------------------------
# Proximity ("who was near the dark vessel"): the chain-of-custody query.
#
# Step 1 (target CTE): find the target MMSI's position at the closest fix to the
#         anchor time ``ts`` within ±``window`` seconds — the place to search
#         around. Picks the single nearest-in-time fix.
# Step 2 (main):       every OTHER vessel that had a fix within ±``window`` of
#         ``ts`` AND whose geom is within ``radius_m`` metres of the target
#         position (true metres via the geography cast -> tracks_geom_idx).
#
# We report each neighbour once, at its closest approach (MIN distance), with the
# fix timestamp at that closest approach. This is what surfaces the STS partner
# that loitered alongside the dark vessel.
# ---------------------------------------------------------------------------
PROXIMITY_SQL = """
WITH target AS (
    SELECT geom, ts
    FROM tracks
    WHERE mmsi = $1
      AND ts BETWEEN to_timestamp($2 - $3) AND to_timestamp($2 + $3)
    ORDER BY abs(extract(epoch FROM ts) - $2) ASC
    LIMIT 1
)
SELECT t.mmsi                                               AS mmsi,
       MIN(ST_Distance(t.geom::geography, tg.geom::geography)) AS min_dist_m,
       (array_agg(
            extract(epoch FROM t.ts)
            ORDER BY ST_Distance(t.geom::geography, tg.geom::geography) ASC
        ))[1]                                               AS closest_ts,
       (array_agg(ST_Y(t.geom)
            ORDER BY ST_Distance(t.geom::geography, tg.geom::geography) ASC))[1] AS lat,
       (array_agg(ST_X(t.geom)
            ORDER BY ST_Distance(t.geom::geography, tg.geom::geography) ASC))[1] AS lon
FROM tracks t
CROSS JOIN target tg
WHERE t.mmsi <> $1
  AND t.ts BETWEEN to_timestamp($2 - $3) AND to_timestamp($2 + $3)
  AND ST_DWithin(t.geom::geography, tg.geom::geography, $4)
GROUP BY t.mmsi
ORDER BY min_dist_m ASC
"""


# Bind-order helpers so callers can't transpose positional args ------------
def proximity_params(
    mmsi: int, ts: float, radius_nm: float, window_min: float
) -> tuple[int, float, float, float]:
    """Positional args for :data:`PROXIMITY_SQL`.

    ($1) mmsi, ($2) anchor epoch ts, ($3) half-window seconds, ($4) radius metres.
    ``window_min`` is the FULL window in minutes (±window_min/2 each side)."""
    half_window_s = (window_min * 60.0) / 2.0
    return (mmsi, ts, half_window_s, nm_to_meters(radius_nm))


def track_params(mmsi: int, t_from: float, t_to: float) -> tuple[int, float, float]:
    """Positional args for :data:`TRACK_SQL` — ($1) mmsi, ($2) from, ($3) to."""
    if t_to < t_from:
        raise ValueError("'to' must be >= 'from'")
    return (mmsi, t_from, t_to)
