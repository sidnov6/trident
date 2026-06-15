"""Fusion seam — out-of-band enrichment adapters for the Analyst.

Each adapter implements the :class:`FusionAdapter` protocol. ``build_adapters``
assembles the active set given the available datastore handles; the Analyst runs
them all and folds their results into the AnalystOutput fusion fields. Adapters
are individually fail-soft: one erroring never breaks the assessment.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from trident_contracts.signal import Signal

from .base import FusionAdapter
from .ofac import OFACFusionAdapter
from .sar import SARFusionAdapter
from .weather import WeatherFusionAdapter

log = logging.getLogger("cognition.fusion")

__all__ = [
    "FusionAdapter",
    "OFACFusionAdapter",
    "SARFusionAdapter",
    "WeatherFusionAdapter",
    "build_adapters",
    "run_fusion",
]


def build_adapters(pool: Any | None = None) -> list[FusionAdapter]:
    """The active adapter set. OFAC is live (cheap local DB lookup); SAR and
    weather are stubs gated on ``fusion_enabled``."""
    return [
        OFACFusionAdapter(pool=pool),
        SARFusionAdapter(pool=pool),
        WeatherFusionAdapter(),
    ]


async def run_fusion(
    adapters: list[FusionAdapter],
    vessel: dict[str, Any],
    signal: Signal,
) -> dict[str, Optional[dict[str, Any]]]:
    """Run every adapter concurrently and key results by adapter ``name``.

    A result of None (or an adapter that raised) maps to None, so the Analyst can
    blindly assign ``output.<name> = results[name]``.
    """
    async def _safe(adapter: FusionAdapter) -> tuple[str, Optional[dict[str, Any]]]:
        try:
            return adapter.name, await adapter.enrich(vessel, signal)
        except Exception as exc:  # pragma: no cover - fusion must never be fatal
            log.warning("fusion adapter %s failed: %s", adapter.name, exc)
            return adapter.name, None

    pairs = await asyncio.gather(*[_safe(a) for a in adapters])
    return dict(pairs)
