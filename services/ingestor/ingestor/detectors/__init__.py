"""The deterministic detector suite (Tier 2).

`build_detectors()` returns the ordered list the pipeline runs on every update
and every slow tick. Adding a detector is one line here.
"""
from __future__ import annotations

from .base import DETECTOR_VERSION, Detector, DetectorContext
from .congestion import CongestionDetector
from .dark_vessel import DarkVesselDetector
from .geofence import GeofenceDetector
from .loitering import LoiteringDetector
from .reroute import RerouteDetector
from .spoofing import SpoofingDetector

__all__ = [
    "DETECTOR_VERSION", "Detector", "DetectorContext", "build_detectors",
    "DarkVesselDetector", "LoiteringDetector", "SpoofingDetector",
    "CongestionDetector", "GeofenceDetector", "RerouteDetector",
]


def build_detectors() -> list[Detector]:
    return [
        DarkVesselDetector(),
        LoiteringDetector(),
        SpoofingDetector(),
        GeofenceDetector(),
        RerouteDetector(),
        CongestionDetector(),
    ]
