"""Single source of truth for Redis key names and stream channels.

Centralising these prevents the classic "ingestor writes vessel:{mmsi}, api reads
vessels:{mmsi}" drift between independently-developed services.
"""
from __future__ import annotations

# --- Redis Streams (the event bus) --------------------------------------
STREAM_SIGNALS = "trident:signals"        # detectors -> cognition + api ticker
STREAM_INCIDENTS = "trident:incidents"    # cognition -> api (push to UI)
CONSUMER_GROUP_COGNITION = "cognition"
CONSUMER_GROUP_API = "api"

# --- Redis hot state -----------------------------------------------------
def vessel_key(mmsi: int) -> str:
    return f"vessel:{mmsi}"


def zone_geo_key(zone: str) -> str:
    """GEOADD index of live vessels per zone (GEOSEARCH for viewport + congestion)."""
    return f"chokepoint:{zone}:geo"


# Global GEO index of EVERY live vessel (lon/lat per MMSI). Backs the worldwide
# map view + viewport (bbox) GEOSEARCH when AIS coverage is global.
GLOBAL_GEO = "vessels:geo"


def zone_baseline_key(zone: str) -> str:
    """EWMA congestion baseline state for a zone."""
    return f"chokepoint:{zone}:baseline"


def zone_count_key(zone: str) -> str:
    return f"chokepoint:{zone}:count"


WATCHLIST_PRIORITY = "watchlist:priority"   # SADD MMSIs an analyst flagged

# vessel TTL — vessels that vanish age out after 30 min
VESSEL_TTL_S = 1800

# how long a vessel:* hash key persists last_seen-per-zone subkeys, etc.
DARK_TRACKER_KEY = "darktrack"             # hash: per-mmsi last_seen_ts
