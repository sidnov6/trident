"""TrackWriter — the async persistence task.

The per-message path enqueues fixes (and static-identity updates) onto bounded
in-memory queues; this task drains them in batches and writes to Postgres on its
own cadence. That decoupling is the rule: DB latency must never stall ingest. If
the queues fill, the OLDEST fixes are dropped (latest-state-wins again — the live
Redis state is always current; tracks are a best-effort history).

Writes:
  * `tracks` hypertable — every fix, geom = ST_SetSRID(ST_MakePoint(lon,lat),4326).
  * `vessels` — upsert latest static identity per MMSI.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque

from trident_contracts import VesselState

log = logging.getLogger("ingestor.writer")

TRACK_QUEUE_MAX = 50_000
BATCH_SIZE = 500
FLUSH_INTERVAL_S = 2.0

_INSERT_TRACK = """
INSERT INTO tracks (ts, mmsi, geom, sog, cog, heading, nav_status, zone)
VALUES (to_timestamp($1), $2, ST_SetSRID(ST_MakePoint($3, $4), 4326),
        $5, $6, $7, $8, $9)
"""

_UPSERT_VESSEL = """
INSERT INTO vessels (mmsi, imo, name, ship_type, flag, destination, draught,
                     length, beam, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now())
ON CONFLICT (mmsi) DO UPDATE SET
    imo         = COALESCE(EXCLUDED.imo, vessels.imo),
    name        = COALESCE(EXCLUDED.name, vessels.name),
    ship_type   = COALESCE(EXCLUDED.ship_type, vessels.ship_type),
    flag        = COALESCE(EXCLUDED.flag, vessels.flag),
    destination = COALESCE(EXCLUDED.destination, vessels.destination),
    draught     = COALESCE(EXCLUDED.draught, vessels.draught),
    length      = COALESCE(EXCLUDED.length, vessels.length),
    beam        = COALESCE(EXCLUDED.beam, vessels.beam),
    updated_at  = now()
"""


class TrackWriter:
    def __init__(self, pg_pool):
        self._pg = pg_pool
        self._tracks: deque = deque(maxlen=TRACK_QUEUE_MAX)
        self._vessels: dict[int, VesselState] = {}     # coalesced static upserts
        self._stop = asyncio.Event()

    # -- enqueue (called on the hot path; cheap, non-blocking) -------------
    def enqueue_fix(self, state: VesselState) -> None:
        self._tracks.append((
            state.last_fix_ts, state.mmsi, state.lon, state.lat,
            state.sog, state.cog, state.heading, state.nav_status, state.zone,
        ))

    def enqueue_vessel(self, state: VesselState) -> None:
        # Latest-state-wins coalescing keyed by MMSI.
        self._vessels[state.mmsi] = state

    # -- drain task --------------------------------------------------------
    async def run(self) -> None:
        if self._pg is None:
            log.warning("TrackWriter: no Postgres pool, running as no-op drain")
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=FLUSH_INTERVAL_S)
            except asyncio.TimeoutError:
                pass
            await self._flush()
        await self._flush()   # final drain on shutdown

    async def _flush(self) -> None:
        if self._pg is None:
            self._tracks.clear()
            self._vessels.clear()
            return

        # ---- tracks ----
        batch = []
        while self._tracks and len(batch) < BATCH_SIZE:
            batch.append(self._tracks.popleft())
        if batch:
            try:
                async with self._pg.acquire() as conn:
                    await conn.executemany(_INSERT_TRACK, batch)
            except Exception:
                log.warning("track batch (%d rows) dropped", len(batch), exc_info=True)

        # ---- vessels (static identity upserts) ----
        if self._vessels:
            rows = [
                (
                    v.mmsi, v.imo, v.name, v.ship_type, v.flag, v.destination,
                    v.draught, v.length, v.beam,
                )
                for v in self._vessels.values()
            ]
            self._vessels.clear()
            try:
                async with self._pg.acquire() as conn:
                    await conn.executemany(_UPSERT_VESSEL, rows)
            except Exception:
                log.warning("vessel upsert (%d rows) dropped", len(rows), exc_info=True)

    def stop(self) -> None:
        self._stop.set()
