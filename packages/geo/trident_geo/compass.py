"""Course-over-ground (degrees) -> human compass direction."""
from __future__ import annotations

from typing import Optional

_POINTS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


def cog_to_compass(cog: Optional[float]) -> Optional[str]:
    """e.g. 200 -> 'SSW'. None / invalid -> None."""
    if cog is None:
        return None
    try:
        c = float(cog) % 360.0
    except (TypeError, ValueError):
        return None
    idx = int((c + 11.25) % 360.0 // 22.5)
    return _POINTS[idx]
