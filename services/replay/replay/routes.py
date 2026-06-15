"""Replay REST + WebSocket surface.

  GET /replay/track/{mmsi}?from=&to=          -> ordered track points
  WS  /replay/stream  (mmsi/from/to/speed)    -> vessel_delta-shaped frames,
                                                 replayed in time order at speed×
  GET /replay/proximity?mmsi=&ts=&radius_nm=&window_min=
                                              -> chain-of-custody neighbours

The WS emits the SAME ``vessel_delta`` frame shape as the api gateway so the
existing map UI renders a replay with zero client changes — the analyst just
points the socket at ``:8100/replay/stream`` instead of ``:8000/ws``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from trident_contracts.enums import bucket_for_ship_type
from trident_contracts.vessel import VesselLite
from trident_contracts.ws import VesselDeltaMsg

from .queries import (
    PROXIMITY_SQL,
    TRACK_SQL,
    proximity_params,
    track_params,
)

log = logging.getLogger("replay.routes")

router = APIRouter()

# Bound replay speed so a runaway ?speed= can't busy-spin the event loop.
MIN_SPEED = 0.1
MAX_SPEED = 240.0
# Cap the simulated inter-fix sleep so a long real-world gap doesn't stall replay.
MAX_STEP_SLEEP_S = 2.0


# --- response models -------------------------------------------------------
class TrackPoint(BaseModel):
    ts: float
    lat: float
    lon: float
    sog: Optional[float] = None
    cog: Optional[float] = None


class ProximityHit(BaseModel):
    """A vessel that came within the radius of the target during the window."""

    mmsi: int
    min_dist_m: float
    min_dist_nm: float
    closest_ts: float
    lat: float
    lon: float


def _pool(request: Request) -> Optional[Any]:
    return getattr(request.app.state, "pool", None)


# --- track fetch -----------------------------------------------------------
@router.get("/replay/track/{mmsi}", response_model=list[TrackPoint])
async def get_track(
    request: Request,
    mmsi: int,
    from_: float = Query(..., alias="from", description="window start, epoch s"),
    to: float = Query(..., description="window end, epoch s"),
) -> list[TrackPoint]:
    """Ordered (ts, lat, lon, sog, cog) for an MMSI in ``[from, to]``."""
    pool = _pool(request)
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(TRACK_SQL, *track_params(mmsi, from_, to))
    except Exception:
        log.warning("track fetch failed for %s", mmsi, exc_info=True)
        return []
    return [
        TrackPoint(
            ts=float(r["ts"]), lat=float(r["lat"]), lon=float(r["lon"]),
            sog=(float(r["sog"]) if r["sog"] is not None else None),
            cog=(float(r["cog"]) if r["cog"] is not None else None),
        )
        for r in rows
        if r["lat"] is not None and r["lon"] is not None
    ]


# --- proximity (chain of custody) -----------------------------------------
@router.get("/replay/proximity", response_model=list[ProximityHit])
async def get_proximity(
    request: Request,
    mmsi: int = Query(..., description="target (e.g. the dark vessel) MMSI"),
    ts: float = Query(..., description="anchor time, epoch s (e.g. blackout start)"),
    radius_nm: float = Query(0.5, ge=0.0, description="search radius, nautical miles"),
    window_min: float = Query(30.0, ge=0.0, description="full time window, minutes"),
) -> list[ProximityHit]:
    """Every vessel within ``radius_nm`` of the target's position at ``ts``
    (±``window_min``/2). Answers "who was near the dark vessel during its
    blackout" — the STS partner falls out as the closest, longest-loitering hit.
    """
    pool = _pool(request)
    if pool is None:
        return []
    from .queries import METERS_PER_NM

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                PROXIMITY_SQL,
                *proximity_params(mmsi, ts, radius_nm, window_min),
            )
    except Exception:
        log.warning("proximity query failed for %s", mmsi, exc_info=True)
        return []
    return [
        ProximityHit(
            mmsi=r["mmsi"],
            min_dist_m=float(r["min_dist_m"]),
            min_dist_nm=float(r["min_dist_m"]) / METERS_PER_NM,
            closest_ts=float(r["closest_ts"]),
            lat=float(r["lat"]),
            lon=float(r["lon"]),
        )
        for r in rows
    ]


# --- replay stream (WebSocket) --------------------------------------------
async def _load_track(pool: Any, mmsi: int, t_from: float, t_to: float) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(TRACK_SQL, *track_params(mmsi, t_from, t_to))
    return [
        {
            "ts": float(r["ts"]), "lat": float(r["lat"]), "lon": float(r["lon"]),
            "sog": float(r["sog"]) if r["sog"] is not None else 0.0,
            "cog": float(r["cog"]) if r["cog"] is not None else 0.0,
        }
        for r in rows
        if r["lat"] is not None and r["lon"] is not None
    ]


async def _ship_bucket(pool: Any, mmsi: int) -> int:
    """ship_type bucket for map colouring (from the ``vessels`` table)."""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT ship_type FROM vessels WHERE mmsi = $1", mmsi
            )
    except Exception:
        row = None
    st = row["ship_type"] if row else None
    return int(bucket_for_ship_type(st))


@router.websocket("/replay/stream")
async def replay_stream(websocket: WebSocket) -> None:
    """Replay a historical track as ``vessel_delta`` frames at ``speed``× realtime.

    Query params: ``mmsi``, ``from``, ``to`` (epoch seconds), ``speed`` (default
    60×). The gap between consecutive fixes is slept for ``(dt / speed)`` seconds
    so a 1 Hz feed at speed=60 plays one real minute per simulated second. Each
    fix is sent as a single-vessel ``vessel_delta`` so the existing UI renders it.
    """
    await websocket.accept()
    pool = getattr(websocket.app.state, "pool", None)

    qp = websocket.query_params
    try:
        mmsi = int(qp["mmsi"])
        t_from = float(qp["from"])
        t_to = float(qp["to"])
    except (KeyError, ValueError):
        await websocket.send_json({"error": "mmsi, from, to are required numerics"})
        await websocket.close(code=1003)
        return
    try:
        speed = float(qp.get("speed", "60"))
    except ValueError:
        speed = 60.0
    speed = max(MIN_SPEED, min(MAX_SPEED, speed))

    if pool is None:
        await websocket.send_json({"error": "no database"})
        await websocket.close(code=1011)
        return

    try:
        track = await _load_track(pool, mmsi, t_from, t_to)
    except Exception:
        log.warning("replay track load failed for %s", mmsi, exc_info=True)
        await websocket.send_json({"error": "track load failed"})
        await websocket.close(code=1011)
        return

    bucket = await _ship_bucket(pool, mmsi)

    # Tell the client what it's about to scrub (frame count + real-time span).
    await websocket.send_json(
        {
            "kind": "replay_meta",
            "mmsi": mmsi,
            "from": t_from,
            "to": t_to,
            "speed": speed,
            "points": len(track),
        }
    )

    try:
        prev_ts: Optional[float] = None
        for fix in track:
            if prev_ts is not None:
                dt = fix["ts"] - prev_ts
                if dt > 0:
                    await asyncio.sleep(min(dt / speed, MAX_STEP_SLEEP_S))
            prev_ts = fix["ts"]

            lite = VesselLite(
                m=mmsi, la=fix["lat"], lo=fix["lon"],
                s=fix["sog"], c=fix["cog"], t=bucket, f=fix["ts"], st=0,
            )
            frame = VesselDeltaMsg(vessels=[lite], ts=fix["ts"]).model_dump()
            await websocket.send_json(frame)

        # Sentinel end-of-replay marker so the UI can reset the scrubber.
        await websocket.send_json({"kind": "replay_end", "mmsi": mmsi})
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover
        log.debug("replay stream ended", exc_info=True)
    finally:
        try:
            await websocket.close()
        except Exception:  # pragma: no cover
            pass
