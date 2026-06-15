"""TRIDENT shared contracts — the frozen interface across all services."""
from .enums import (
    STATUS_BIT_DARK,
    STATUS_BIT_GEOFENCE,
    STATUS_BIT_LOITERING,
    STATUS_BIT_SPOOF,
    STATUS_BIT_WATCHLIST,
    IncidentStatus,
    ShipTypeBucket,
    SignalType,
    ThreatLevel,
    Typology,
    bucket_for_ship_type,
)
from .incident import (
    AnalystOutput,
    AuditEntry,
    DeskOutput,
    Incident,
    SentinelOutput,
)
from .signal import Signal, SignalLite
from .vessel import VesselDossier, VesselLite, VesselState
from .ws import (
    IncidentMsg,
    SignalTickMsg,
    VesselDeltaMsg,
    WSMessage,
    ZoneStatsMsg,
)

__all__ = [
    "SignalType", "Typology", "IncidentStatus", "ThreatLevel", "ShipTypeBucket",
    "bucket_for_ship_type",
    "STATUS_BIT_DARK", "STATUS_BIT_LOITERING", "STATUS_BIT_WATCHLIST",
    "STATUS_BIT_GEOFENCE", "STATUS_BIT_SPOOF",
    "Signal", "SignalLite",
    "VesselState", "VesselLite", "VesselDossier",
    "Incident", "SentinelOutput", "AnalystOutput", "DeskOutput", "AuditEntry",
    "VesselDeltaMsg", "SignalTickMsg", "IncidentMsg", "ZoneStatsMsg", "WSMessage",
]

__version__ = "1.0.0"
