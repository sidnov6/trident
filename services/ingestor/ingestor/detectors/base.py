"""Detector base class + the context object passed on every tick.

A detector is pure with respect to its inputs: given the same context (world
state + clock) it produces the same Signals. Each carries DETECTOR_VERSION so a
Signal can be reproduced from the schema version that emitted it.

Two evaluation shapes are supported:
  * `on_update(ctx, mmsi)` — per-vessel detectors, called when MMSI just moved.
  * `on_tick(ctx)`         — zone-level detectors (congestion, U-turn cluster),
                             called on a slow cadence independent of any message.
A detector may implement either or both; the base returns no signals by default.
"""
from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import TYPE_CHECKING

from trident_contracts import Signal

if TYPE_CHECKING:
    from ..state import VesselStateEngine

# Bumping this invalidates reproducibility expectations for ALL detectors; bump
# only when detection semantics change, not for unrelated refactors.
DETECTOR_VERSION = "1.0.0"


@dataclass
class DetectorContext:
    """Everything a detector is allowed to touch on a tick."""

    state: "VesselStateEngine"
    redis: object            # redis.asyncio client | None
    now: float               # epoch seconds — the detector's notion of "now"


class Detector(ABC):
    """Abstract detector. Subclasses override on_update and/or on_tick."""

    name: str = "detector"
    version: str = DETECTOR_VERSION

    async def on_update(self, ctx: DetectorContext, mmsi: int) -> list[Signal]:
        """React to a single vessel having just produced a new fix."""
        return []

    async def on_tick(self, ctx: DetectorContext) -> list[Signal]:
        """React on a slow cadence (zone aggregates). Default: nothing."""
        return []
