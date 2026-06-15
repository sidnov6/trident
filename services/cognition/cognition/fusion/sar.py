"""SAR (Synthetic Aperture Radar) fusion adapter — SEAM STUB.

A dark vessel is *invisible* to AIS by definition. Radar is not fooled: a
Sentinel-1 scene over the same patch of water at the same time either shows a
hull there or it does not. A positive SAR hit is the strongest possible
confirmation that a dark vessel is real and not a feed gap.

Currently a no-op (returns None unless ``fusion_enabled``).

Real-world build-out (TODO):
  * Query the Copernicus Data Space Ecosystem STAC / OData catalogue for
    Sentinel-1 GRD scenes whose footprint contains the signal position and whose
    acquisition time is within +/- a few minutes of ``signal.ts``.
  * Authenticate with ``settings.copernicus_user`` / ``copernicus_password``.
  * On a footprint+time match, run (or look up) a CFAR ship-detection pass over
    the scene; if a detection falls within ~1 NM of the AIS-dark position, emit
    the scene id, mission, acquisition time and catalogue URL, and persist a row
    into ``sar_scenes`` linked to the incident.
"""
from __future__ import annotations

from typing import Any, Optional

from trident_common.settings import get_settings
from trident_contracts.signal import Signal


class SARFusionAdapter:
    name = "sar_confirmation"

    def __init__(self, pool: Any | None = None) -> None:
        self._pool = pool

    async def enrich(
        self,
        vessel: dict[str, Any],
        signal: Signal,
    ) -> Optional[dict[str, Any]]:
        if not get_settings().fusion_enabled:
            return None
        # TODO: Copernicus Sentinel-1 space-time catalogue query at signal.position.
        return None
