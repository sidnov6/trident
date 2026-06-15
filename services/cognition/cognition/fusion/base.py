"""The fusion-adapter seam.

Fusion adapters enrich an Analyst assessment with out-of-band intelligence —
sanctions hits, SAR (radar) confirmation of a dark vessel, marine weather, OSINT.
They are structured now and built out later. Every adapter is a no-op that
returns ``None`` unless ``settings.fusion_enabled`` is set, so the swarm runs
fully offline today and lights up these joins later without touching the graph.

The contract is intentionally tiny: an async ``enrich(vessel, signal) -> dict |
None``. A ``None`` return means "no enrichment" and the Analyst leaves the
corresponding fusion field unset.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from trident_contracts.signal import Signal


@runtime_checkable
class FusionAdapter(Protocol):
    """Structural type every fusion source implements.

    ``name`` keys the result into the right AnalystOutput field
    (sanctions_match / sar_confirmation / weather_context / osint_context).
    """

    name: str

    async def enrich(
        self,
        vessel: dict[str, Any],
        signal: Signal,
    ) -> Optional[dict[str, Any]]:
        """Return source-specific enrichment, or None when there is nothing to add."""
        ...
