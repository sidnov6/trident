"""Shared enumerations for the TRIDENT platform.

These are the closed vocabularies that every service agrees on. Changing a value
here is a breaking change across ingestor, cognition, api and web.
"""
from __future__ import annotations

from enum import Enum


class SignalType(str, Enum):
    """Typed detector firings emitted by the Tier-2 reflex suite."""

    DARK_VESSEL = "DARK_VESSEL"
    LOITERING = "LOITERING"
    POSITION_JUMP = "POSITION_JUMP"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
    CONGESTION = "CONGESTION"
    GEOFENCE_BREACH = "GEOFENCE_BREACH"
    REROUTE = "REROUTE"
    UTURN = "UTURN"


class Typology(str, Enum):
    """Threat classification assigned by the Analyst agent."""

    SANCTIONS_EVASION = "SANCTIONS_EVASION"
    STS_TRANSFER = "STS_TRANSFER"
    SMUGGLING_COVER = "SMUGGLING_COVER"
    NAV_HAZARD = "NAV_HAZARD"
    MILITARY_ACTIVITY = "MILITARY_ACTIVITY"
    BENIGN = "BENIGN"


class IncidentStatus(str, Enum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    ACTIONED = "actioned"


class ThreatLevel(str, Enum):
    """NORAD-style per-chokepoint posture derived from live signal severity."""

    GREEN = "GREEN"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ShipTypeBucket(int, Enum):
    """Coarse vessel-class buckets used for map colouring.

    Derived from the AIS ship_type code (see `bucket_for_ship_type`).
    """

    OTHER = 0
    TANKER = 1
    CARGO = 2
    PASSENGER = 3
    FISHING = 4
    HIGH_SPEED = 5
    TUG_SPECIAL = 6


def bucket_for_ship_type(ship_type: int | None) -> ShipTypeBucket:
    """Map a raw AIS ship_type code to a coarse colour bucket."""
    if ship_type is None:
        return ShipTypeBucket.OTHER
    if 80 <= ship_type <= 89:
        return ShipTypeBucket.TANKER
    if 70 <= ship_type <= 79:
        return ShipTypeBucket.CARGO
    if 60 <= ship_type <= 69:
        return ShipTypeBucket.PASSENGER
    if ship_type == 30:
        return ShipTypeBucket.FISHING
    if 40 <= ship_type <= 49:
        return ShipTypeBucket.HIGH_SPEED
    if ship_type in (31, 32, 33, 35, 50, 51, 52, 53, 54, 55):
        return ShipTypeBucket.TUG_SPECIAL
    return ShipTypeBucket.OTHER


# Status bitfield used in VesselLite.st (hot path on the wire).
STATUS_BIT_DARK = 1 << 0
STATUS_BIT_LOITERING = 1 << 1
STATUS_BIT_WATCHLIST = 1 << 2
STATUS_BIT_GEOFENCE = 1 << 3
STATUS_BIT_SPOOF = 1 << 4
