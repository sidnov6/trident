"""Read the vessel hot-state the ingestor mirrors into Redis.

The ingestor writes, per MMSI (see ingestor/state.py):
  * ``HSET vessel:{mmsi} ...``  — a flat string map of ``VesselState`` fields
    (Nones dropped, non-scalars JSON-encoded), with ``EXPIRE VESSEL_TTL_S``.
  * ``GEOADD chokepoint:{zone}:geo lon lat mmsi`` — the per-zone GEO index used
    for viewport / congestion reads.

This module reads that back consistently and projects it to the wire form the UI
wants: :class:`VesselLite`. It never imports ingestor code — it re-reads Redis.

Two read paths:
  * :meth:`all_active_vessels` — full snapshot (SCAN over ``vessel:*``).
  * :meth:`viewport_vessels`   — bbox-scoped, resolved through the per-zone GEO
    index (``GEOSEARCH``) so we only touch vessels plausibly in view.

Status bits (:data:`STATUS_BIT_*`) are derived cheaply from current state +
watchlist membership; richer flags (spoof, geofence) are stamped by detectors and
are out of scope for a pure-Redis read, so we surface what state supports.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from trident_common import keys
from trident_contracts.enums import (
    STATUS_BIT_DARK,
    STATUS_BIT_LOITERING,
    STATUS_BIT_WATCHLIST,
    bucket_for_ship_type,
)
from trident_contracts.vessel import VesselLite, VesselState
from trident_geo import CHOKEPOINTS

log = logging.getLogger("api.state_reader")

# A vessel with no fix newer than this (seconds) reads as "dark" on the hot path.
DARK_AFTER_S = 900.0


def _coerce(raw: dict[Any, Any]) -> dict[str, Any]:
    """Rebuild a ``VesselState`` field dict from a Redis hash.

    The hash stores scalars verbatim and JSON-encoded compound values; bytes may
    appear if the client wasn't created with ``decode_responses=True``. We decode
    keys/values and let pydantic coerce the scalar types.
    """
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if isinstance(k, bytes):
            k = k.decode()
        if isinstance(v, bytes):
            v = v.decode()
        out[k] = v
    return out


def state_from_hash(raw: dict[Any, Any]) -> Optional[VesselState]:
    """Validate a Redis ``vessel:{mmsi}`` hash into a :class:`VesselState`.

    Returns None for an empty / unparsable hash (e.g. a key that expired between
    SCAN and HGETALL)."""
    data = _coerce(raw)
    if not data or "mmsi" not in data:
        return None
    # ``zone`` was JSON-encoded by the ingestor only if non-scalar; it is a plain
    # string here. Defensive: strip accidental JSON quoting.
    z = data.get("zone")
    if isinstance(z, str) and len(z) >= 2 and z[0] == '"' and z[-1] == '"':
        try:
            data["zone"] = json.loads(z)
        except json.JSONDecodeError:
            pass
    try:
        return VesselState.model_validate(data)
    except Exception:  # pragma: no cover - corrupt/partial hash
        log.debug("unparsable vessel hash: %s", data, exc_info=True)
        return None


def status_bits(state: VesselState, *, now: float, watchlist: set[int]) -> int:
    """Derive the :class:`VesselLite` status bitfield from current state.

    Pure function of the latest fix + watchlist membership — no history needed:
      * DARK      — last fix older than :data:`DARK_AFTER_S`.
      * LOITERING — effectively stationary (very low SOG) while inside a zone.
      * WATCHLIST — MMSI flagged by an analyst (``keys.WATCHLIST_PRIORITY``).
    """
    bits = 0
    if state.last_fix_ts and (now - state.last_fix_ts) > DARK_AFTER_S:
        bits |= STATUS_BIT_DARK
    if state.zone and state.sog < 0.5:
        bits |= STATUS_BIT_LOITERING
    if state.mmsi in watchlist:
        bits |= STATUS_BIT_WATCHLIST
    return bits


def to_lite(state: VesselState, *, now: float, watchlist: set[int]) -> VesselLite:
    """Project a :class:`VesselState` to the wire-form :class:`VesselLite`."""
    bucket = bucket_for_ship_type(state.ship_type)
    return VesselLite.from_state(
        state,
        bucket=int(bucket),
        status_bits=status_bits(state, now=now, watchlist=watchlist),
    )


class StateReader:
    """Stateless reader over the Redis hot-state. One per app, holds the client."""

    def __init__(self, redis: Any):
        self._redis = redis

    # -- watchlist ---------------------------------------------------------
    async def watchlist(self) -> set[int]:
        """Current analyst-priority MMSIs (``keys.WATCHLIST_PRIORITY`` SET)."""
        if self._redis is None:
            return set()
        try:
            members = await self._redis.smembers(keys.WATCHLIST_PRIORITY)
        except Exception:
            return set()
        out: set[int] = set()
        for m in members or ():
            if isinstance(m, bytes):
                m = m.decode()
            try:
                out.add(int(m))
            except (TypeError, ValueError):
                continue
        return out

    # -- full snapshot -----------------------------------------------------
    async def all_active_vessels(self) -> list[VesselState]:
        """SCAN every live ``vessel:*`` hash and return parsed states.

        Uses SCAN (cursor) not KEYS so a large keyspace never blocks Redis.
        """
        if self._redis is None:
            return []
        states: list[VesselState] = []
        try:
            async for key in self._redis.scan_iter(match="vessel:*", count=500):
                raw = await self._redis.hgetall(key)
                st = state_from_hash(raw)
                if st is not None:
                    states.append(st)
        except Exception:
            log.warning("vessel SCAN failed", exc_info=True)
        return states

    async def _states_for_mmsis(self, mmsis: list[int]) -> list[VesselState]:
        """HGETALL a specific set of MMSIs (used by the GEO viewport path)."""
        states: list[VesselState] = []
        for mmsi in mmsis:
            try:
                raw = await self._redis.hgetall(keys.vessel_key(mmsi))
            except Exception:
                continue
            st = state_from_hash(raw)
            if st is not None:
                states.append(st)
        return states

    # -- viewport (bbox) ---------------------------------------------------
    async def viewport_vessels(
        self, bbox: tuple[float, float, float, float]
    ) -> list[VesselState]:
        """Vessels intersecting ``bbox = (min_lat, min_lon, max_lat, max_lon)``.

        Resolved per-zone through ``GEOSEARCH`` over each ``zone_geo_key`` whose
        chokepoint bbox overlaps the requested viewport, then HGETALL'd and
        filtered to the exact box. Zones outside the viewport are skipped, so a
        zoomed-in map only touches the relevant GEO index.
        """
        if self._redis is None:
            return []
        min_lat, min_lon, max_lat, max_lon = bbox
        seen: set[int] = set()
        mmsis: list[int] = []

        for cp in CHOKEPOINTS:
            (sw_lat, sw_lon), (ne_lat, ne_lon) = cp.bbox
            # Skip zones whose bbox doesn't intersect the viewport at all.
            if ne_lat < min_lat or sw_lat > max_lat:
                continue
            if ne_lon < min_lon or sw_lon > max_lon:
                continue
            # GEOSEARCH a box centred on the zone centre that covers it fully.
            c_lat = (sw_lat + ne_lat) / 2.0
            c_lon = (sw_lon + ne_lon) / 2.0
            # width/height in metres (rough: 1 deg lat ~= 111_320 m).
            height_m = (ne_lat - sw_lat) * 111_320.0
            width_m = (ne_lon - sw_lon) * 111_320.0
            try:
                members = await self._redis.geosearch(
                    keys.zone_geo_key(cp.id),
                    longitude=c_lon,
                    latitude=c_lat,
                    width=max(width_m, 1.0),
                    height=max(height_m, 1.0),
                    unit="m",
                )
            except Exception:
                continue
            for m in members or ():
                if isinstance(m, bytes):
                    m = m.decode()
                try:
                    mmsi = int(m)
                except (TypeError, ValueError):
                    continue
                if mmsi not in seen:
                    seen.add(mmsi)
                    mmsis.append(mmsi)

        states = await self._states_for_mmsis(mmsis)
        # Exact bbox filter (GEO box is an over-approximation).
        return [
            s for s in states
            if min_lat <= s.lat <= max_lat and min_lon <= s.lon <= max_lon
        ]

    # -- zone-scoped snapshot ---------------------------------------------
    async def zone_vessels(self, zone: str) -> list[VesselState]:
        """All live vessels whose current zone == ``zone`` (via the GEO index)."""
        if self._redis is None:
            return []
        try:
            members = await self._redis.zrange(keys.zone_geo_key(zone), 0, -1)
        except Exception:
            members = []
        mmsis: list[int] = []
        for m in members or ():
            if isinstance(m, bytes):
                m = m.decode()
            try:
                mmsis.append(int(m))
            except (TypeError, ValueError):
                continue
        states = await self._states_for_mmsis(mmsis)
        # The GEO index can retain a member after its vessel hash expired; the
        # HGETALL above already dropped those. Confirm the zone for freshness.
        return [s for s in states if s.zone == zone]

    async def zone_count(self, zone: str) -> int:
        """Live count in a zone (cardinality of the GEO sorted set)."""
        if self._redis is None:
            return 0
        try:
            return int(await self._redis.zcard(keys.zone_geo_key(zone)))
        except Exception:
            return 0

    # -- lite projections (the WS / REST hot path) -------------------------
    async def snapshot_lite(
        self, zone: Optional[str] = None, *, now: float
    ) -> list[VesselLite]:
        """A :class:`VesselLite` snapshot, optionally scoped to one ``zone``."""
        watch = await self.watchlist()
        states = (
            await self.zone_vessels(zone) if zone else await self.all_active_vessels()
        )
        return [to_lite(s, now=now, watchlist=watch) for s in states]
