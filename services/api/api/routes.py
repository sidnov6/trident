"""REST surface of the api gateway.

Snapshots only — the live push lives on ``/ws`` (see api/ws.py). Everything here
is a read of the spine: Redis hot-state for the live vessel snapshot, Postgres
for the durable record (dossier track, incidents, audit, signals backfill).

Routes:
  GET /vessels?zone=                 -> list[VesselLite]   (current snapshot)
  GET /vessels/{mmsi}                -> VesselDossier
  GET /incidents?limit=&zone=&status=-> list[Incident]
  GET /incidents/{id}               -> {incident, audit}   (full case file)
  GET /zones                        -> list[ZoneInfo]      (metadata + live posture)
  GET /signals?limit=&zone=         -> list[SignalLite]    (ticker backfill)

The Postgres pool and the Redis-backed StateReader hang off ``app.state`` and are
fetched per-request, so every handler degrades gracefully when a datastore is
absent (returns empty rather than 500).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from trident_contracts.enums import ThreatLevel
from trident_contracts.incident import Incident
from trident_contracts.signal import SignalLite, SignalType
from trident_contracts.vessel import VesselDossier, VesselLite
from trident_geo import CHOKEPOINTS, flag_for_mmsi

from .threat import threat_for_zone

log = logging.getLogger("api.routes")

router = APIRouter()


# --- response models the contracts don't already define --------------------
class ZoneInfo(BaseModel):
    """Per-chokepoint metadata + live posture for the zone-rail UI."""

    id: str
    name: str
    center: tuple[float, float]      # (lat, lon) for fly-to
    bbox: tuple[tuple[float, float], tuple[float, float]]
    count: int = 0                   # live vessel count
    z: float = 0.0                   # congestion z-score
    threat_level: str = ThreatLevel.GREEN.value


# --- small Redis/PG helpers ------------------------------------------------
def _reader(request: Request):
    return request.app.state.reader


def _pool(request: Request) -> Optional[Any]:
    return getattr(request.app.state, "pool", None)


def _redis(request: Request) -> Optional[Any]:
    return getattr(request.app.state, "redis", None)


async def _zone_z(redis: Optional[Any], zone: str) -> float:
    """Best-effort congestion z-score: (count - baseline_mean) / baseline_std.

    The ingestor maintains the EWMA baseline under ``zone_baseline_key``; we read
    it back if present. Shape is implementation-defined there, so we parse
    defensively and fall back to 0.0 (which renders as 'nominal')."""
    if redis is None:
        return 0.0
    from trident_common import keys

    try:
        raw = await redis.get(keys.zone_baseline_key(zone))
    except Exception:
        return 0.0
    if not raw:
        return 0.0
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return 0.0
    if isinstance(data, dict):
        try:
            return float(data.get("z", 0.0))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


async def _recent_zone_signals(
    pool: Optional[Any], zone: str, *, window_s: float = 3600.0
) -> list[tuple[float, float]]:
    """``(epoch_ts, severity)`` pairs for a zone's recent signals (threat input)."""
    if pool is None:
        return []
    cutoff = time.time() - window_s
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT extract(epoch FROM ts) AS ts, severity
                FROM signals
                WHERE zone = $1 AND ts >= to_timestamp($2)
                ORDER BY ts DESC
                LIMIT 500
                """,
                zone,
                cutoff,
            )
    except Exception:
        return []
    return [(float(r["ts"]), float(r["severity"] or 0.0)) for r in rows]


# --- vessels ---------------------------------------------------------------
@router.get("/vessels", response_model=list[VesselLite])
async def get_vessels(
    request: Request,
    zone: Optional[str] = Query(default=None, description="chokepoint id filter"),
) -> list[VesselLite]:
    """Current vessel snapshot (Redis hot-state), optionally scoped to a zone."""
    return await _reader(request).snapshot_lite(zone=zone, now=time.time())


@router.get("/vessels/{mmsi}", response_model=VesselDossier)
async def get_vessel(request: Request, mmsi: int) -> VesselDossier:
    """Full dossier: static identity (Redis state / ``vessels`` table) + recent
    track (``tracks``) + incident ids (``incidents``)."""
    pool = _pool(request)

    # Static identity — prefer fresh Redis state, fall back to the vessels table.
    dossier = VesselDossier(mmsi=mmsi)
    from trident_common import keys

    redis = _redis(request)
    state = None
    if redis is not None:
        try:
            raw = await redis.hgetall(keys.vessel_key(mmsi))
        except Exception:
            raw = None
        if raw:
            from .state_reader import state_from_hash

            state = state_from_hash(raw)
    if state is not None:
        dossier.imo = state.imo
        dossier.name = state.name
        dossier.flag = state.flag or flag_for_mmsi(mmsi)
        dossier.ship_type = state.ship_type
        dossier.destination = state.destination
        dossier.draught = state.draught
        dossier.length = state.length
        dossier.beam = state.beam
        dossier.first_seen_ts = state.first_seen_ts or None
        dossier.last_fix_ts = state.last_fix_ts or None

    if pool is not None:
        try:
            async with pool.acquire() as conn:
                if state is None:
                    vrow = await conn.fetchrow(
                        """
                        SELECT imo, name, ship_type, flag, destination,
                               draught, length, beam
                        FROM vessels WHERE mmsi = $1
                        """,
                        mmsi,
                    )
                    if vrow:
                        dossier.imo = vrow["imo"]
                        dossier.name = vrow["name"]
                        dossier.ship_type = vrow["ship_type"]
                        dossier.flag = vrow["flag"] or flag_for_mmsi(mmsi)
                        dossier.destination = vrow["destination"]
                        dossier.draught = vrow["draught"]
                        dossier.length = vrow["length"]
                        dossier.beam = vrow["beam"]

                # Recent track (newest 500 fixes), returned oldest-first.
                trows = await conn.fetch(
                    """
                    SELECT extract(epoch FROM ts) AS ts,
                           ST_Y(geom) AS lat, ST_X(geom) AS lon
                    FROM tracks
                    WHERE mmsi = $1
                    ORDER BY ts DESC
                    LIMIT 500
                    """,
                    mmsi,
                )
                dossier.track = [
                    (float(r["ts"]), float(r["lat"]), float(r["lon"]))
                    for r in reversed(trows)
                    if r["lat"] is not None and r["lon"] is not None
                ]

                irows = await conn.fetch(
                    "SELECT id::text FROM incidents WHERE mmsi = $1 "
                    "ORDER BY opened_at DESC",
                    mmsi,
                )
                dossier.incident_ids = [r["id"] for r in irows]
        except Exception:
            log.warning("dossier DB read failed for %s", mmsi, exc_info=True)

    if dossier.flag is None:
        dossier.flag = flag_for_mmsi(mmsi)
    return dossier


# --- incidents -------------------------------------------------------------
def _incident_from_payload(payload: Any) -> Optional[Incident]:
    if payload is None:
        return None
    if isinstance(payload, (str, bytes)):
        try:
            return Incident.model_validate_json(payload)
        except Exception:
            return None
    if isinstance(payload, dict):
        try:
            return Incident.model_validate(payload)
        except Exception:
            return None
    return None


@router.get("/incidents", response_model=list[Incident])
async def get_incidents(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    zone: Optional[str] = None,
    status: Optional[str] = None,
) -> list[Incident]:
    """Recent incidents, reconstructed from ``incidents.payload`` (the full
    Incident object cognition persists)."""
    pool = _pool(request)
    if pool is None:
        return []
    clauses: list[str] = []
    args: list[Any] = []
    if zone:
        args.append(zone)
        clauses.append(f"zone = ${len(args)}")
    if status:
        args.append(status)
        clauses.append(f"status = ${len(args)}")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    args.append(limit)
    sql = (
        f"SELECT payload FROM incidents{where} "
        f"ORDER BY opened_at DESC LIMIT ${len(args)}"
    )
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
    except Exception:
        log.warning("incident list query failed", exc_info=True)
        return []
    out: list[Incident] = []
    for r in rows:
        inc = _incident_from_payload(r["payload"])
        if inc is not None:
            out.append(inc)
    return out


@router.get("/incidents/{incident_id}")
async def get_incident(request: Request, incident_id: str) -> dict[str, Any]:
    """Full case file: the Incident (with agent outputs) + its audit trail."""
    pool = _pool(request)
    if pool is None:
        return {"incident": None, "audit": []}
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT payload FROM incidents WHERE id = $1::uuid", incident_id
            )
            audit_rows = await conn.fetch(
                """
                SELECT agent, input_hash, output, model, prompt_version,
                       extract(epoch FROM ts) AS ts
                FROM audit_log
                WHERE incident_id = $1::uuid
                ORDER BY ts ASC
                """,
                incident_id,
            )
    except Exception:
        log.warning("incident detail query failed for %s", incident_id, exc_info=True)
        return {"incident": None, "audit": []}

    inc = _incident_from_payload(row["payload"]) if row else None
    audit: list[dict[str, Any]] = []
    for a in audit_rows:
        out = a["output"]
        if isinstance(out, (str, bytes)):
            try:
                out = json.loads(out)
            except (json.JSONDecodeError, TypeError):
                pass
        audit.append(
            {
                "agent": a["agent"],
                "input_hash": a["input_hash"],
                "output": out,
                "model": a["model"],
                "prompt_version": a["prompt_version"],
                "ts": float(a["ts"]) if a["ts"] is not None else None,
            }
        )
    return {
        "incident": inc.model_dump(mode="json") if inc else None,
        "audit": audit,
    }


# --- zones -----------------------------------------------------------------
@router.get("/zones", response_model=list[ZoneInfo])
async def get_zones(request: Request) -> list[ZoneInfo]:
    """Per-chokepoint metadata + live count, congestion z, and threat level."""
    reader = _reader(request)
    redis = _redis(request)
    pool = _pool(request)
    out: list[ZoneInfo] = []
    for cp in CHOKEPOINTS:
        count = await reader.zone_count(cp.id)
        z = await _zone_z(redis, cp.id)
        sigs = await _recent_zone_signals(pool, cp.id)
        level = threat_for_zone(sigs)
        out.append(
            ZoneInfo(
                id=cp.id,
                name=cp.name,
                center=cp.center,
                bbox=cp.bbox,
                count=count,
                z=z,
                threat_level=level.value,
            )
        )
    return out


# --- signals (ticker backfill) ---------------------------------------------
@router.get("/signals", response_model=list[SignalLite])
async def get_signals(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    zone: Optional[str] = None,
) -> list[SignalLite]:
    """Recent signals from the durable ``signals`` table — the ticker's backfill
    so a freshly-connected client sees history before the live tail kicks in."""
    pool = _pool(request)
    if pool is None:
        return []
    args: list[Any] = []
    where = ""
    if zone:
        args.append(zone)
        where = " WHERE zone = $1"
    args.append(limit)
    sql = (
        f"SELECT id::text, extract(epoch FROM ts) AS ts, type, mmsi, zone, severity "
        f"FROM signals{where} ORDER BY ts DESC LIMIT ${len(args)}"
    )
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
    except Exception:
        log.warning("signals backfill query failed", exc_info=True)
        return []
    out: list[SignalLite] = []
    for r in rows:
        try:
            out.append(
                SignalLite(
                    id=r["id"],
                    ts=float(r["ts"]),
                    type=SignalType(r["type"]),
                    mmsi=r["mmsi"],
                    zone=r["zone"],
                    severity=float(r["severity"] or 0.0),
                )
            )
        except Exception:
            continue
    return out
