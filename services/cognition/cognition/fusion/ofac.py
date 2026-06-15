"""OFAC / OpenSanctions fusion adapter.

This is the one adapter wired *live* today, because the join is cheap and
requires no external network call: a confirmed dark vessel whose MMSI or IMO
appears in the ``sanctions_vessels`` table is a step-change in confidence for the
SANCTIONS_EVASION typology. If the table is empty (the default until the
sanctions loader is run) this returns None and nothing changes.

Real-world build-out (TODO): the ``sanctions_vessels`` table is populated by a
periodic loader that consolidates the OFAC SDN maritime list and the
OpenSanctions consolidated dataset, keyed by IMO (stable) and MMSI (volatile).
This adapter then does the point-lookup join below; matching on IMO is preferred
since shadow-fleet vessels rotate MMSIs.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from trident_common.settings import get_settings
from trident_contracts.signal import Signal

log = logging.getLogger("cognition.fusion.ofac")


class OFACFusionAdapter:
    name = "sanctions_match"

    def __init__(self, pool: Any | None = None) -> None:
        # ``pool`` is an asyncpg pool (or None offline). Live even when
        # fusion_enabled is False, because this is a local DB lookup, not an
        # external API — a sanctions hit is too important to gate behind a flag.
        self._pool = pool

    async def enrich(
        self,
        vessel: dict[str, Any],
        signal: Signal,
    ) -> Optional[dict[str, Any]]:
        if self._pool is None:
            return None
        imo = vessel.get("imo")
        mmsi = vessel.get("mmsi") or signal.mmsi
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT imo, mmsi, name, flag, program, owner, source
                    FROM sanctions_vessels
                    WHERE ($1::bigint IS NOT NULL AND imo = $1)
                       OR ($2::bigint IS NOT NULL AND mmsi = $2)
                    ORDER BY (imo = $1) DESC
                    LIMIT 1
                    """,
                    imo,
                    mmsi,
                )
        except Exception as exc:  # pragma: no cover - DB optional / table absent
            log.debug("OFAC lookup skipped (%s)", exc)
            return None
        if row is None:
            return None
        hit = dict(row)
        hit["matched_on"] = "imo" if (imo and hit.get("imo") == imo) else "mmsi"
        log.info("Sanctions hit for mmsi=%s (program=%s)", mmsi, hit.get("program"))
        return hit
