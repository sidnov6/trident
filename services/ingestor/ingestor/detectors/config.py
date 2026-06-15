"""Single home for every detector threshold.

Defaults live here; per-zone overrides are pulled from the frozen geo layer
(`CHOKEPOINTS[*].gap_threshold_s`) or supplied explicitly. Keeping the numbers
in one place is what makes the detectors auditable and tunable without touching
detection logic.
"""
from __future__ import annotations

from dataclasses import dataclass

from trident_geo import CHOKEPOINTS_BY_ID

# -- dark vessel -----------------------------------------------------------
DEFAULT_GAP_THRESHOLD_S = 1800          # fallback if a zone has no override
DARK_MIN_SOG_KN = 0.5                   # a moving vessel that goes silent is the signal
REAPPEAR_MIN_DISPLACEMENT_NM = 5.0      # jump on reappearance that implies dark-period transit
REAPPEAR_MIN_GAP_MIN = 5.0             # ignore micro-gaps on reappearance

# -- spoofing --------------------------------------------------------------
MAX_PLAUSIBLE_KN = 40.0                 # implied speed above this = teleport
IDENTITY_CONFLICT_MIN_NM = 0.5          # same MMSI, same ts, this far apart = two emitters

# -- loitering -------------------------------------------------------------
LOITER_WINDOW_S = 60 * 60               # 60-minute sliding window
LOITER_MAX_DISPLACEMENT_NM = 2.0        # stays within a 2nm box
LOITER_MAX_MEAN_SOG_KN = 1.5            # and barely moving
LOITER_MIN_FIXES = 4                    # need enough samples to trust the window

# -- congestion ------------------------------------------------------------
CONGESTION_Z = 3.0                      # z-score over EWMA baseline
EWMA_ALPHA = 0.05                       # baseline smoothing factor
EWMA_MIN_SAMPLES = 20                   # warm-up before z-scores are trusted
# Counts are integers; a baseline that settles to near-zero variance would make
# any +1 vessel look like an infinite z-score. Floor the std at 1 vessel.
EWMA_MIN_VAR = 1.0

# -- reroute / u-turn ------------------------------------------------------
UTURN_MIN_DEG = 150.0                   # course reversal threshold
UTURN_SUSTAIN_FIXES = 3                 # reversal must persist over N fixes
UTURN_CLUSTER_WINDOW_S = 15 * 60        # window for aggregating U-turns in a zone
UTURN_CLUSTER_MIN_COUNT = 3             # this many U-turns -> zone-level cluster


@dataclass(frozen=True)
class ZoneConfig:
    zone: str
    gap_threshold_s: int


def zone_config(zone: str) -> ZoneConfig:
    """Per-zone thresholds, falling back to defaults for unknown zones."""
    cp = CHOKEPOINTS_BY_ID.get(zone)
    return ZoneConfig(
        zone=zone,
        gap_threshold_s=cp.gap_threshold_s if cp else DEFAULT_GAP_THRESHOLD_S,
    )


def gap_threshold_for(zone: str | None) -> int:
    if zone is None:
        return DEFAULT_GAP_THRESHOLD_S
    return zone_config(zone).gap_threshold_s
