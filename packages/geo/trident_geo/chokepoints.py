"""Chokepoint definitions and geo helpers shared across services.

CRITICAL: AISStream bounding-box corners are [latitude, longitude] and each box
is [[swLat, swLon], [neLat, neLon]]. Getting this wrong subscribes you to empty
ocean. This module is the single source of truth for those boxes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chokepoint:
    id: str
    name: str
    # [[swLat, swLon], [neLat, neLon]]  (latitude first!)
    bbox: tuple[tuple[float, float], tuple[float, float]]
    # core ring inset (degrees) — silence inside the core means "went dark";
    # silence near the bbox edge means "left coverage".
    edge_buffer_deg: float = 0.03
    # gap threshold seconds for the dark-vessel detector (dense canals are tighter)
    gap_threshold_s: int = 1800
    center: tuple[float, float] = (0.0, 0.0)  # (lat, lon) for fly-to


CHOKEPOINTS: list[Chokepoint] = [
    Chokepoint(
        id="suez",
        name="Suez Canal",
        bbox=((29.85, 32.25), (31.35, 32.65)),
        edge_buffer_deg=0.03,
        gap_threshold_s=900,            # 15 min — reports are frequent, silence is loud
        center=(30.55, 32.35),
    ),
    Chokepoint(
        id="hormuz",
        name="Strait of Hormuz",
        bbox=((25.90, 55.30), (27.10, 57.10)),
        gap_threshold_s=1800,
        center=(26.50, 56.20),
    ),
    Chokepoint(
        id="bab_el_mandeb",
        name="Bab-el-Mandeb",
        bbox=((12.30, 43.10), (13.10, 43.70)),
        gap_threshold_s=1800,
        center=(12.70, 43.40),
    ),
    Chokepoint(
        id="malacca",
        name="Strait of Malacca",
        bbox=((1.00, 98.00), (6.00, 104.00)),
        gap_threshold_s=1800,
        center=(3.50, 101.00),
    ),
    Chokepoint(
        id="panama",
        name="Panama Canal",
        bbox=((8.85, -80.05), (9.45, -79.45)),
        gap_threshold_s=1200,
        center=(9.15, -79.75),
    ),
    Chokepoint(
        id="bosphorus",
        name="Bosphorus",
        bbox=((40.90, 28.90), (41.30, 29.20)),
        gap_threshold_s=900,
        center=(41.10, 29.05),
    ),
]

CHOKEPOINTS_BY_ID: dict[str, Chokepoint] = {c.id: c for c in CHOKEPOINTS}

# The list AISStream's subscription message expects.
CHOKEPOINT_BOXES: list[list[list[float]]] = [
    [list(c.bbox[0]), list(c.bbox[1])] for c in CHOKEPOINTS
]


def validate_boxes() -> None:
    """Fail fast on the classic [lat, lon] inversion bug."""
    for c in CHOKEPOINTS:
        (sw_lat, sw_lon), (ne_lat, ne_lon) = c.bbox
        assert -90 <= sw_lat <= 90 and -90 <= ne_lat <= 90, f"{c.id}: lat out of range"
        assert -180 <= sw_lon <= 180 and -180 <= ne_lon <= 180, f"{c.id}: lon out of range"
        assert sw_lat < ne_lat, f"{c.id}: sw_lat must be < ne_lat (corners are [lat, lon])"
        assert sw_lon < ne_lon, f"{c.id}: sw_lon must be < ne_lon"


def zone_for_point(lat: float, lon: float) -> str | None:
    """Return the chokepoint id containing (lat, lon), or None."""
    for c in CHOKEPOINTS:
        (sw_lat, sw_lon), (ne_lat, ne_lon) = c.bbox
        if sw_lat <= lat <= ne_lat and sw_lon <= lon <= ne_lon:
            return c.id
    return None


def near_edge(lat: float, lon: float, c: Chokepoint) -> bool:
    """True if the point is within edge_buffer_deg of the bbox boundary —
    i.e. it likely just left coverage rather than going dark."""
    (sw_lat, sw_lon), (ne_lat, ne_lon) = c.bbox
    b = c.edge_buffer_deg
    return (
        lat - sw_lat < b or ne_lat - lat < b or
        lon - sw_lon < b or ne_lon - lon < b
    )
