"""The single multiplexed WebSocket: ``/ws``.

One connection carries four message kinds, all defined in
``trident_contracts.ws``:

  * ``vessel_delta`` — periodic snapshot of the *changed* VesselLite set, built
    from Redis hot-state. ~1–2 Hz. Coalesced latest-state-wins (never a backlog).
  * ``signal_tick``  — tail of ``keys.STREAM_SIGNALS`` -> SignalLite per firing.
  * ``incident``     — tail of ``keys.STREAM_INCIDENTS`` -> Incident.
  * ``zone_stats``   — per-zone count / z / threat_level on a timer.

Architecture — fan-in to a bounded per-client queue
---------------------------------------------------
Each connected client gets a :class:`ClientHub`. Independent producer tasks
(vessel-delta builder, signal tailer, incident tailer, zone-stats timer) push
frames into the hub; a single writer task drains the hub to the socket. The
**hard rule** (INTEGRATION.md #2 — coalesce, don't queue) is enforced
structurally:

  * vessel deltas are coalesced into a single pending dict keyed by MMSI
    (latest-state-wins) — an arbitrarily slow client never accumulates a backlog
    of stale positions; it just gets the freshest set on the next drain.
  * signal/incident frames go onto a small bounded deque; on overflow the OLDEST
    are dropped (the durable record is in Postgres, the ticker can backfill).

The signal/incident stream tailers are *shared* across all clients (one
XREADGROUP loop fanning out), so N clients don't open N consumer reads.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from trident_common import keys
from trident_contracts.fleet_alert import FleetAlert
from trident_contracts.incident import Incident
from trident_contracts.signal import Signal, SignalLite
from trident_contracts.vessel import VesselLite
from trident_contracts.ws import (
    FleetAlertMsg,
    IncidentMsg,
    SignalTickMsg,
    VesselDeltaMsg,
    ZoneStatsMsg,
)
from trident_geo import CHOKEPOINTS

from .state_reader import StateReader
from .threat import threat_for_zone

log = logging.getLogger("api.ws")

ws_router = APIRouter()

# --- cadences --------------------------------------------------------------
VESSEL_DELTA_HZ = 1.0            # vessel_delta tick rate
ZONE_STATS_INTERVAL_S = 5.0     # zone_stats timer
STREAM_BLOCK_MS = 2000          # XREAD block window for the shared tailers

# Bounded per-client backlog of discrete (signal/incident) frames.
EVENT_BACKLOG_MAX = 256

# Consumer name for this api instance's stream reads.
_CONSUMER_NAME = "api-1"


class ClientHub:
    """Per-connection fan-in: coalesced vessel deltas + a bounded event deque."""

    def __init__(self) -> None:
        # Latest-state-wins coalesce buffer for vessel deltas, keyed by MMSI.
        self._pending_vessels: dict[int, VesselLite] = {}
        # Bounded discrete-event backlog (signal_tick / incident / zone_stats).
        self._events: deque[dict[str, Any]] = deque(maxlen=EVENT_BACKLOG_MAX)
        self._wake = asyncio.Event()
        # Per-client viewport (min_lat, min_lon, max_lat, max_lon). Defaults to the
        # whole world so something renders before the client reports its camera; the
        # client narrows it on every map move so we only stream in-view ships.
        self.viewport: tuple[float, float, float, float] = (-85.0, -180.0, 85.0, 180.0)

    def set_viewport(self, bbox: tuple[float, float, float, float]) -> None:
        self.viewport = bbox

    # -- producers ---------------------------------------------------------
    def push_vessels(self, vessels: list[VesselLite]) -> None:
        for v in vessels:
            self._pending_vessels[v.m] = v   # overwrite => coalesce
        self._wake.set()

    def push_event(self, frame: dict[str, Any]) -> None:
        # maxlen deque drops the OLDEST on overflow — never an unbounded queue.
        self._events.append(frame)
        self._wake.set()

    # -- consumer ----------------------------------------------------------
    async def drain(self) -> list[dict[str, Any]]:
        """Wait for work, then return all currently-pending frames at once.

        Vessel deltas are collapsed into a single batched ``vessel_delta`` frame
        (the whole changed set this tick); discrete events follow in order.
        """
        await self._wake.wait()
        self._wake.clear()
        frames: list[dict[str, Any]] = []
        if self._pending_vessels:
            vessels = list(self._pending_vessels.values())
            self._pending_vessels.clear()
            frames.append(
                VesselDeltaMsg(vessels=vessels, ts=time.time()).model_dump()
            )
        while self._events:
            frames.append(self._events.popleft())
        return frames


class StreamFanout:
    """Shared tailers for the two Redis streams, fanning out to all client hubs.

    One XREADGROUP loop per stream regardless of client count. New clients
    register their hub; the loops push decoded frames into every registered hub.
    """

    def __init__(self, redis: Optional[Any]) -> None:
        self._redis = redis
        self._hubs: set[ClientHub] = set()
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()

    def register(self, hub: ClientHub) -> None:
        self._hubs.add(hub)

    def unregister(self, hub: ClientHub) -> None:
        self._hubs.discard(hub)

    def _broadcast(self, frame: dict[str, Any]) -> None:
        for hub in self._hubs:
            hub.push_event(frame)

    async def start(self) -> None:
        if self._redis is None:
            log.warning("StreamFanout: no Redis — signal/incident tailers disabled.")
            return
        await self._ensure_group(keys.STREAM_SIGNALS)
        await self._ensure_group(keys.STREAM_INCIDENTS)
        await self._ensure_group(keys.STREAM_FLEET_ALERTS)
        self._tasks = [
            asyncio.create_task(self._tail_signals(), name="ws-tail-signals"),
            asyncio.create_task(self._tail_incidents(), name="ws-tail-incidents"),
            asyncio.create_task(self._tail_alerts(), name="ws-tail-alerts"),
        ]

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # pragma: no cover
                pass

    async def _ensure_group(self, stream: str) -> None:
        """Create the api consumer group (+ stream) if absent (idempotent)."""
        try:
            await self._redis.xgroup_create(
                name=stream,
                groupname=keys.CONSUMER_GROUP_API,
                id="$",          # only NEW messages; backfill is a REST concern
                mkstream=True,
            )
        except Exception as exc:  # BUSYGROUP if it already exists — benign
            if "BUSYGROUP" not in str(exc):  # pragma: no cover
                log.warning("xgroup_create(%s) failed: %s", stream, exc)

    async def _read_group(self, stream: str) -> list[tuple[str, dict]]:
        resp = await self._redis.xreadgroup(
            groupname=keys.CONSUMER_GROUP_API,
            consumername=_CONSUMER_NAME,
            streams={stream: ">"},
            count=64,
            block=STREAM_BLOCK_MS,
        )
        out: list[tuple[str, dict]] = []
        for _stream, messages in resp or ():
            for msg_id, fields in messages:
                out.append((msg_id, fields))
        return out

    async def _tail_signals(self) -> None:
        while not self._stop.is_set():
            try:
                msgs = await self._read_group(keys.STREAM_SIGNALS)
            except Exception as exc:  # pragma: no cover - transient redis
                log.warning("signal tail XREADGROUP failed (%s)", exc)
                await asyncio.sleep(1.0)
                continue
            for msg_id, fields in msgs:
                try:
                    sig = Signal.from_stream_fields(fields)
                    frame = SignalTickMsg(
                        signal=SignalLite.from_signal(sig)
                    ).model_dump(mode="json")
                    self._broadcast(frame)
                except Exception:
                    log.debug("bad signal frame %s", msg_id, exc_info=True)
                finally:
                    try:
                        await self._redis.xack(
                            keys.STREAM_SIGNALS, keys.CONSUMER_GROUP_API, msg_id
                        )
                    except Exception:  # pragma: no cover
                        pass

    async def _tail_incidents(self) -> None:
        while not self._stop.is_set():
            try:
                msgs = await self._read_group(keys.STREAM_INCIDENTS)
            except Exception as exc:  # pragma: no cover
                log.warning("incident tail XREADGROUP failed (%s)", exc)
                await asyncio.sleep(1.0)
                continue
            for msg_id, fields in msgs:
                try:
                    raw = fields.get("payload") or fields.get(b"payload")
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    inc = Incident.model_validate_json(raw)
                    frame = IncidentMsg(incident=inc).model_dump(mode="json")
                    self._broadcast(frame)
                except Exception:
                    log.debug("bad incident frame %s", msg_id, exc_info=True)
                finally:
                    try:
                        await self._redis.xack(
                            keys.STREAM_INCIDENTS, keys.CONSUMER_GROUP_API, msg_id
                        )
                    except Exception:  # pragma: no cover
                        pass

    async def _tail_alerts(self) -> None:
        """Relay fleetscan FleetAlerts to every client as `fleet_alert` frames."""
        while not self._stop.is_set():
            try:
                msgs = await self._read_group(keys.STREAM_FLEET_ALERTS)
            except Exception as exc:  # pragma: no cover - transient redis
                log.warning("fleet-alert tail XREADGROUP failed (%s)", exc)
                await asyncio.sleep(1.0)
                continue
            for msg_id, fields in msgs:
                try:
                    alert = FleetAlert.from_stream_fields(fields)
                    frame = FleetAlertMsg(alert=alert).model_dump(mode="json")
                    self._broadcast(frame)
                except Exception:
                    log.debug("bad fleet alert frame %s", msg_id, exc_info=True)
                finally:
                    try:
                        await self._redis.xack(
                            keys.STREAM_FLEET_ALERTS, keys.CONSUMER_GROUP_API, msg_id
                        )
                    except Exception:  # pragma: no cover
                        pass


async def _vessel_delta_loop(hub: ClientHub, reader: StateReader) -> None:
    """Per-client: rebuild the VesselLite snapshot and push it on a timer.

    A full snapshot each tick keeps the client correct without server-side diff
    bookkeeping; coalescing in the hub guarantees a slow client only ever holds
    the latest set. Kept tiny by VesselLite's terse fields.
    """
    interval = 1.0 / VESSEL_DELTA_HZ
    while True:
        try:
            # Stream only the ships in THIS client's current viewport (global feed
            # has tens of thousands of vessels — never push them all).
            lite = await reader.viewport_lite(hub.viewport, now=time.time())
            if lite:
                hub.push_vessels(lite)
        except Exception:  # pragma: no cover
            log.debug("vessel delta build failed", exc_info=True)
        await asyncio.sleep(interval)


async def _zone_stats_loop(
    hub: ClientHub, reader: StateReader, pool: Optional[Any]
) -> None:
    """Per-client: per-zone count / z / threat_level on a timer."""
    while True:
        for cp in CHOKEPOINTS:
            try:
                count = await reader.zone_count(cp.id)
                sigs = await _recent_zone_signals(pool, cp.id)
                level = threat_for_zone(sigs)
                frame = ZoneStatsMsg(
                    zone=cp.id,
                    count=count,
                    z=0.0,
                    transit_min=None,
                    threat_level=level.value,
                ).model_dump()
                hub.push_event(frame)
            except Exception:  # pragma: no cover
                log.debug("zone stats build failed for %s", cp.id, exc_info=True)
        await asyncio.sleep(ZONE_STATS_INTERVAL_S)


async def _recent_zone_signals(
    pool: Optional[Any], zone: str, *, window_s: float = 3600.0
) -> list[tuple[float, float]]:
    """``(epoch_ts, severity)`` pairs feeding the zone's threat level."""
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
                ORDER BY ts DESC LIMIT 500
                """,
                zone,
                cutoff,
            )
    except Exception:
        return []
    return [(float(r["ts"]), float(r["severity"] or 0.0)) for r in rows]


@ws_router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    """The one socket. Spins up per-client producer tasks fanning into a hub,
    plus a single writer task draining the hub to the wire."""
    await websocket.accept()
    app = websocket.app
    reader: StateReader = app.state.reader
    pool = getattr(app.state, "pool", None)
    fanout: StreamFanout = app.state.fanout

    hub = ClientHub()
    fanout.register(hub)

    producers = [
        asyncio.create_task(_vessel_delta_loop(hub, reader)),
        asyncio.create_task(_zone_stats_loop(hub, reader, pool)),
    ]

    async def _writer() -> None:
        while True:
            frames = await hub.drain()
            for frame in frames:
                await websocket.send_json(frame)

    writer = asyncio.create_task(_writer())

    try:
        # The client streams its camera here: {"kind":"viewport","bbox":[minLat,
        # minLon,maxLat,maxLon]} on every map move. Anything else just keeps the
        # socket alive / lets us detect close.
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                if msg.get("kind") == "viewport":
                    b = msg.get("bbox") or []
                    if len(b) == 4:
                        hub.set_viewport((float(b[0]), float(b[1]), float(b[2]), float(b[3])))
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover
        log.debug("ws receive loop ended", exc_info=True)
    finally:
        fanout.unregister(hub)
        for t in (*producers, writer):
            t.cancel()
        for t in (*producers, writer):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # pragma: no cover
                pass
