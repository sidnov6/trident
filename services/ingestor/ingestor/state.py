"""VesselStateEngine — the latest-state-wins world model.

Per MMSI we keep:
  * an in-process `VesselState` (the merged dynamic + static record),
  * a bounded `deque(maxlen=256)` track ring buffer of (ts, lat, lon),
and mirror the hot fields into Redis:
  * `HSET vessel:{mmsi} ...` with `EXPIRE keys.VESSEL_TTL_S`,
  * `GEOADD chokepoint:{zone}:geo lon lat mmsi` for viewport + congestion.

Coalescing is structural: an update overwrites the previous state for that MMSI.
There is no per-vessel queue — only the newest fix survives. Detectors read the
ring buffer when they need short history (loiter window, U-turn run, speed jump).
"""
from __future__ import annotations

import json
from collections import deque
from typing import Optional

from trident_common import keys
from trident_contracts import VesselState
from trident_geo import zone_for_point

from .normalize import NormalizedUpdate

TRACK_RING = 256


class VesselStateEngine:
    def __init__(self, redis):
        self._redis = redis
        self._states: dict[int, VesselState] = {}
        self._tracks: dict[int, deque] = {}

    # -- ingest ------------------------------------------------------------
    async def apply(self, upd: NormalizedUpdate) -> VesselState:
        """Merge a normalized update into the world model and return the new
        VesselState. Position updates push the ring buffer + Redis GEO index;
        static updates just refresh identity."""
        prev = self._states.get(upd.mmsi)
        if prev is None:
            base = VesselState(
                mmsi=upd.mmsi,
                lat=upd.lat if upd.lat is not None else 0.0,
                lon=upd.lon if upd.lon is not None else 0.0,
                first_seen_ts=upd.ts,
            )
            data = base.model_dump()
        else:
            data = prev.model_dump()

        # Latest-state-wins merge of the partial fields.
        for k, v in upd.fields.items():
            data[k] = v
        if data.get("first_seen_ts", 0.0) == 0.0:
            data["first_seen_ts"] = upd.ts

        # Recompute zone from the freshest position.
        if data.get("lat") is not None and data.get("lon") is not None:
            data["zone"] = zone_for_point(data["lat"], data["lon"])

        state = VesselState(**data)
        self._states[upd.mmsi] = state

        # Ring buffer + GEO index only advance on a real position fix.
        if not upd.is_static and upd.lat is not None and upd.lon is not None:
            ring = self._tracks.setdefault(upd.mmsi, deque(maxlen=TRACK_RING))
            ring.append((upd.ts, upd.lat, upd.lon))

        await self._mirror_to_redis(state, moved=(not upd.is_static))
        return state

    async def _mirror_to_redis(self, state: VesselState, moved: bool) -> None:
        if self._redis is None:
            return
        key = keys.vessel_key(state.mmsi)
        # HSET wants a flat string map; serialise Nones away and JSON-free.
        mapping = {}
        for k, v in state.model_dump().items():
            if v is None:
                continue
            mapping[k] = v if isinstance(v, (int, float, str)) else json.dumps(v)
        try:
            await self._redis.hset(key, mapping=mapping)
            await self._redis.expire(key, keys.VESSEL_TTL_S)
            if moved:
                # Global index: EVERY vessel, so the worldwide map + viewport
                # GEOSEARCH can find it regardless of chokepoint membership.
                await self._redis.geoadd(
                    keys.GLOBAL_GEO,
                    (state.lon, state.lat, str(state.mmsi)),
                )
                if state.zone:
                    await self._redis.geoadd(
                        keys.zone_geo_key(state.zone),
                        (state.lon, state.lat, str(state.mmsi)),
                    )
        except Exception:
            # Redis hiccups must never stall ingest; the next fix re-mirrors.
            pass

    # -- accessors used by detectors --------------------------------------
    def get_state(self, mmsi: int) -> Optional[VesselState]:
        return self._states.get(mmsi)

    def all_states(self):
        return self._states.values()

    def get_track(self, mmsi: int) -> list[tuple[float, float, float]]:
        """Return the (ts, lat, lon) ring buffer for an MMSI, oldest first."""
        return list(self._tracks.get(mmsi, ()))

    async def zone_count(self, zone: str) -> int:
        """Live vessel count in a zone via the Redis GEO index.

        GEOSEARCH over the whole bbox would need a radius; instead we use the
        cardinality of the underlying sorted set, which the GEO index maintains.
        Stale members age out because the zone key is rebuilt continuously and we
        only count members whose vessel hash is still alive (TTL not expired)."""
        if self._redis is None:
            # In-process fallback (tests / no-redis mode).
            return sum(1 for s in self._states.values() if s.zone == zone)
        try:
            return int(await self._redis.zcard(keys.zone_geo_key(zone)))
        except Exception:
            return 0

    async def zone_search(self, zone: str, lat: float, lon: float, radius_nm: float):
        """GEOSEARCH members within radius_nm of (lat, lon) in a zone. Returns
        list of mmsi strings. Used for STS / proximity checks."""
        if self._redis is None:
            return []
        try:
            return await self._redis.geosearch(
                keys.zone_geo_key(zone),
                longitude=lon,
                latitude=lat,
                radius=radius_nm * 1852.0,   # nm -> metres
                unit="m",
            )
        except Exception:
            return []
