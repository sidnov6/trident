"""All fleetscan thresholds + composite-risk weights in one place."""
from __future__ import annotations

# --- sweep cadence ----------------------------------------------------------
SCAN_INTERVAL_S = 10.0          # how often we sweep the whole world
SWEEP_BATCH = 500              # MMSIs per pipelined HGETALL batch
MEMORY_TTL_S = 1800.0          # prune per-mmsi memory after this much silence

# --- per-agent thresholds ---------------------------------------------------
GONE_DARK_GAP_S = 1800.0       # silent longer than this while it was moving
MOVING_SOG = 0.5              # "was underway" speed floor
TELEPORT_KN = 40.0            # implied speed above this = position jump/spoof
LOITER_SOG = 0.6             # below this counts as loitering
LOITER_SWEEPS = 4            # consecutive low-sog sweeps before we flag
STS_RADIUS_NM = 0.6          # rendezvous proximity
DRAUGHT_TANKER_M = 8.0        # untyped vessel this deep + has IMO -> likely tanker

# AIS nav_status codes we treat specially
NAV_AT_ANCHOR = 1
NAV_MOORED = 5
NAV_AGROUND = 6
NAV_UNDEFINED = 15

GREY_ZONE_NAME_TOKENS = ("NAVY", "WARSHIP", "WAR SHIP", "COAST GUARD", "COASTGUARD",
                          "PATROL", "NAVAL", "FRIGATE", "DESTROYER", "CORVETTE")

# --- dedupe / cooldown ------------------------------------------------------
COOLDOWN_S = 600              # same (mmsi, category) at most once per 10 min
ESCALATION_DELTA = 0.2       # severity jump that bypasses cooldown

# --- composite risk ---------------------------------------------------------
CATEGORY_WEIGHT = {
    "SANCTIONS_RISK": 1.00,
    "NAV_HAZARD": 0.95,
    "DARK_FLEET": 0.90,
    "GONE_DARK": 0.85,
    "SPOOFING": 0.85,
    "STS_TRANSFER": 0.80,
    "GREY_ZONE": 0.70,
    "LOITERING": 0.55,
}
CORROBORATION_BOOST = 0.15    # per extra category scoring > 0.3
FOC_MULTIPLIER = 0.15        # flag-of-convenience amplifies composite risk

# --- breadcrumbs ------------------------------------------------------------
BREADCRUMB_MAX = 240         # capped path length per flagged vessel
RISK_FLAG_THRESHOLD = 0.6    # risk at/above this -> persist a breadcrumb + watchlist
