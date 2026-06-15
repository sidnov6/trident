"""Decode the AISStream message envelope into a partial VesselState update.

AISStream (and our synthetic source, which mimics it exactly) wraps every
message like::

    {
      "MessageType": "PositionReport",
      "MetaData": {"MMSI": 636092123, "latitude": 30.1, "longitude": 32.5,
                   "time_utc": "2024-01-01 00:00:00.000000000 +0000 UTC"},
      "Message": {"PositionReport": { ... raw AIS fields ... }}
    }

We emit a `NormalizedUpdate`: the MMSI, an epoch timestamp, an (lat, lon) when
present, and a dict of VesselState *partial* fields (only what this message
carries). The state engine merges that — latest-state-wins — onto the world
model. PositionReport carries dynamics; ShipStaticData carries identity.

Field access is case-tolerant: AISStream uses PascalCase keys, but we accept
lower/upper variants so the synthetic source and any field-name drift survive.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from trident_geo import flag_for_mmsi


@dataclass
class NormalizedUpdate:
    mmsi: int
    ts: float                                  # epoch seconds
    lat: Optional[float] = None
    lon: Optional[float] = None
    fields: dict[str, Any] = field(default_factory=dict)  # partial VesselState
    is_static: bool = False                    # ShipStaticData vs PositionReport


def _get(d: dict, *names: str) -> Any:
    """Return the first present key among `names`, trying case variants."""
    if not isinstance(d, dict):
        return None
    for name in names:
        for cand in (name, name.lower(), name.upper(), name.capitalize()):
            if cand in d:
                return d[cand]
    return None


def _parse_time(meta: dict) -> float:
    """AISStream MetaData.time_utc -> epoch seconds. Falls back to now()."""
    raw = _get(meta, "time_utc", "timeUtc", "TimeUtc")
    if raw is None:
        return time.time()
    if isinstance(raw, (int, float)):
        return float(raw)
    # AISStream format: "2024-01-01 12:00:00.000000000 +0000 UTC"
    s = str(raw).replace(" UTC", "").strip()
    # Python's %f tops out at 6 digits; AISStream sends 9 nanosecond digits.
    # Clamp the fractional part to 6 before parsing.
    if "." in s:
        head, rest = s.split(".", 1)
        parts = rest.split(" ", 1)          # ["000000000", "+0000"]
        frac = parts[0][:6]
        suffix = (" " + parts[1]) if len(parts) > 1 else ""
        s = f"{head}.{frac}{suffix}"
    for fmt in ("%Y-%m-%d %H:%M:%S.%f %z", "%Y-%m-%d %H:%M:%S %z"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return time.time()


def normalize(envelope: dict) -> Optional[NormalizedUpdate]:
    """Turn one AISStream envelope into a NormalizedUpdate, or None if it's not a
    message we model (e.g. an Error frame or an unknown type)."""
    if not isinstance(envelope, dict):
        return None

    msg_type = _get(envelope, "MessageType", "message_type")
    meta = _get(envelope, "MetaData", "metadata") or {}
    body = _get(envelope, "Message", "message") or {}

    mmsi = _get(meta, "MMSI", "mmsi") or _get(body, "UserID", "user_id")
    if mmsi is None:
        return None
    try:
        mmsi = int(mmsi)
    except (ValueError, TypeError):
        return None

    ts = _parse_time(meta)
    # MetaData carries an authoritative lat/lon on both message kinds.
    lat = _get(meta, "latitude", "Latitude", "lat")
    lon = _get(meta, "longitude", "Longitude", "lon")

    if msg_type in ("PositionReport", "position_report"):
        return _normalize_position(mmsi, ts, lat, lon, _get(body, "PositionReport") or body)
    if msg_type in ("ShipStaticData", "ship_static_data"):
        return _normalize_static(mmsi, ts, lat, lon, _get(body, "ShipStaticData") or body)
    return None


def _normalize_position(
    mmsi: int, ts: float, lat: Any, lon: Any, pr: dict
) -> Optional[NormalizedUpdate]:
    plat = _get(pr, "Latitude", "latitude", "lat")
    plon = _get(pr, "Longitude", "longitude", "lon")
    if lat is None:
        lat = plat
    if lon is None:
        lon = plon
    if lat is None or lon is None:
        return None

    fields: dict[str, Any] = {
        "mmsi": mmsi,
        "lat": float(lat),
        "lon": float(lon),
        "last_fix_ts": ts,
    }

    sog = _get(pr, "Sog", "sog", "SOG", "speed")
    if sog is not None:
        fields["sog"] = float(sog)
    cog = _get(pr, "Cog", "cog", "COG", "course")
    if cog is not None:
        fields["cog"] = float(cog)
    hdg = _get(pr, "TrueHeading", "trueHeading", "heading", "Heading")
    if hdg is not None and float(hdg) != 511:   # 511 = heading-not-available
        fields["heading"] = float(hdg)
    nav = _get(pr, "NavigationalStatus", "navigationalStatus", "nav_status", "navStatus")
    if nav is not None:
        fields["nav_status"] = int(nav)

    flag = flag_for_mmsi(mmsi)
    if flag:
        fields["flag"] = flag

    return NormalizedUpdate(mmsi=mmsi, ts=ts, lat=float(lat), lon=float(lon), fields=fields)


def _normalize_static(
    mmsi: int, ts: float, lat: Any, lon: Any, sd: dict
) -> NormalizedUpdate:
    fields: dict[str, Any] = {"mmsi": mmsi}

    name = _get(sd, "Name", "name", "ShipName")
    if name is not None:
        fields["name"] = str(name).strip()
    imo = _get(sd, "ImoNumber", "imoNumber", "imo", "IMO")
    if imo is not None:
        try:
            imo_i = int(imo)
            if imo_i > 0:
                fields["imo"] = imo_i
        except (ValueError, TypeError):
            pass
    stype = _get(sd, "Type", "type", "ship_type", "shipType")
    if stype is not None:
        fields["ship_type"] = int(stype)
    dest = _get(sd, "Destination", "destination")
    if dest is not None:
        fields["destination"] = str(dest).strip()
    draught = _get(sd, "MaximumStaticDraught", "maximumStaticDraught", "draught", "Draught")
    if draught is not None:
        fields["draught"] = float(draught)

    # Dimensions: A (bow) + B (stern) = length; C (port) + D (starboard) = beam.
    dim = _get(sd, "Dimension", "dimension") or {}
    a = _get(dim, "A", "a")
    b = _get(dim, "B", "b")
    c = _get(dim, "C", "c")
    d = _get(dim, "D", "d")
    if a is not None and b is not None:
        fields["length"] = float(a) + float(b)
    if c is not None and d is not None:
        fields["beam"] = float(c) + float(d)

    flag = flag_for_mmsi(mmsi)
    if flag:
        fields["flag"] = flag

    f_lat = float(lat) if lat is not None else None
    f_lon = float(lon) if lon is not None else None
    return NormalizedUpdate(
        mmsi=mmsi, ts=ts, lat=f_lat, lon=f_lon, fields=fields, is_static=True
    )
