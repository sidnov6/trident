"""Geo layer — chokepoint boxes, Suez surgical zones, flag-from-MMSI, geofencing."""
from __future__ import annotations

import json
from importlib import resources

from .chokepoints import (
    CHOKEPOINT_BOXES,
    CHOKEPOINTS,
    CHOKEPOINTS_BY_ID,
    Chokepoint,
    near_edge,
    validate_boxes,
    zone_for_point,
)
from .flags import flag_for_mmsi, is_flag_of_convenience

__all__ = [
    "CHOKEPOINTS", "CHOKEPOINTS_BY_ID", "CHOKEPOINT_BOXES", "Chokepoint",
    "near_edge", "validate_boxes", "zone_for_point",
    "flag_for_mmsi", "is_flag_of_convenience",
    "load_zone_geojson", "list_zone_files",
]


def list_zone_files() -> list[str]:
    files = resources.files(__package__).joinpath("zones")
    return sorted(p.name for p in files.iterdir() if p.name.endswith(".geojson"))


def load_zone_geojson(filename: str) -> dict:
    """Load a GeoJSON FeatureCollection from the packaged zones/ directory."""
    text = resources.files(__package__).joinpath("zones", filename).read_text()
    return json.loads(text)
