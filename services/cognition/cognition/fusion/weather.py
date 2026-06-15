"""Marine-weather fusion adapter — SEAM STUB.

Context that separates intent from circumstance: a vessel loitering in a Force-8
gale is probably sheltering, not conducting a clandestine ship-to-ship transfer.
Weather at the incident position lets the Analyst discount benign explanations.

Currently a no-op (returns None unless ``fusion_enabled``).

Real-world build-out (TODO):
  * Call the Open-Meteo Marine API (no key required) at ``signal.position`` for
    the hour of ``signal.ts``: wave height, wind speed/direction, swell.
  * Normalise into a small dict (e.g. {"wave_height_m": .., "wind_kt": ..,
    "sea_state": "rough"}) and let the Analyst fold it into its reasoning —
    high sea state lowers the confidence of an STS_TRANSFER read.
"""
from __future__ import annotations

from typing import Any, Optional

from trident_common.settings import get_settings
from trident_contracts.signal import Signal


class WeatherFusionAdapter:
    name = "weather_context"

    def __init__(self) -> None:
        pass

    async def enrich(
        self,
        vessel: dict[str, Any],
        signal: Signal,
    ) -> Optional[dict[str, Any]]:
        if not get_settings().fusion_enabled:
            return None
        # TODO: Open-Meteo Marine API lookup at the vessel position / signal hour.
        return None
