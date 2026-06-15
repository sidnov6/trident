"""Per-zone ThreatLevel derivation from recent live signal severity.

The map paints each chokepoint with a NORAD-style posture (GREEN / ELEVATED /
HIGH / CRITICAL). That posture is a function of how *alarming* and how *recent*
the detector firings in that zone have been — not a raw count, so one stale scare
doesn't keep a zone red forever and a burst of fresh high-severity firings lights
it up immediately.

Formula — time-decayed maximum severity
---------------------------------------
For each recent signal ``i`` in the zone with severity ``sev_i ∈ [0,1]`` and age
``age_i`` seconds, its *effective* severity decays exponentially with a half-life
of :data:`HALF_LIFE_S` (default 600 s = 10 min)::

    w_i      = 0.5 ** (age_i / HALF_LIFE_S)          # 1.0 now -> 0.5 at 10 min
    eff_i    = sev_i * w_i
    score    = max(eff_i over the window)            # decaying-max, not a sum

We use a decaying **max** (the single most-alarming-still-fresh firing) rather
than a sum so that a swarm of low-severity congestion pings can't masquerade as a
single high-severity dark-vessel event. The score is then bucketed:

    score >= 0.80  -> CRITICAL
    score >= 0.55  -> HIGH
    score >= 0.30  -> ELEVATED
    else           -> GREEN

The same function backs both the REST ``/zones`` threat_level field and the
``zone_stats`` WebSocket frames, so the UI is always self-consistent.
"""
from __future__ import annotations

import time

from trident_contracts.enums import ThreatLevel

# Half-life of a signal's contribution to the posture, in seconds.
HALF_LIFE_S: float = 600.0

# Only signals fresher than this contribute at all (older ones decayed to noise).
WINDOW_S: float = 3600.0

# Bucket thresholds on the decaying-max effective severity.
_CRITICAL = 0.80
_HIGH = 0.55
_ELEVATED = 0.30


def decayed_score(
    signals: list[tuple[float, float]],
    *,
    now: float | None = None,
) -> float:
    """Decaying-max effective severity over ``(ts, severity)`` pairs.

    ``signals`` is a list of ``(epoch_ts, severity)`` tuples for one zone.
    Returns a score in ``[0, 1]``. Empty input -> 0.0 (GREEN).
    """
    if not signals:
        return 0.0
    now = time.time() if now is None else now
    best = 0.0
    for ts, sev in signals:
        age = now - ts
        if age < 0:
            age = 0.0
        if age > WINDOW_S:
            continue
        weight = 0.5 ** (age / HALF_LIFE_S)
        eff = sev * weight
        if eff > best:
            best = eff
    return best


def level_for_score(score: float) -> ThreatLevel:
    """Bucket a decaying-max score into a :class:`ThreatLevel`."""
    if score >= _CRITICAL:
        return ThreatLevel.CRITICAL
    if score >= _HIGH:
        return ThreatLevel.HIGH
    if score >= _ELEVATED:
        return ThreatLevel.ELEVATED
    return ThreatLevel.GREEN


def threat_for_zone(
    signals: list[tuple[float, float]],
    *,
    now: float | None = None,
) -> ThreatLevel:
    """Convenience: score then bucket ``(ts, severity)`` pairs for one zone."""
    return level_for_score(decayed_score(signals, now=now))
