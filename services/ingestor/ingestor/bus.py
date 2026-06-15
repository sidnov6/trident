"""SignalBus — publishes Signals onto the event bus and into Postgres.

Every Signal goes two places:
  * `XADD trident:signals * payload <json>` — the live event bus that cognition
    and the api ticker consume (via Signal.to_stream_fields()).
  * an INSERT into the `signals` Postgres table — the durable evidence trail.

The Postgres write is best-effort and must never block the bus: a Signal that
reaches Redis has been "delivered" for the live pipeline; the DB row is for
audit. We await both but swallow DB errors so a slow database can't wedge ingest.
"""
from __future__ import annotations

import json
import logging

from trident_common import keys
from trident_contracts import Signal

log = logging.getLogger("ingestor.bus")

_INSERT_SIGNAL = """
INSERT INTO signals (id, ts, type, mmsi, zone, severity, confidence, geom,
                     evidence, detector_version)
VALUES ($1, to_timestamp($2), $3, $4, $5, $6, $7,
        ST_SetSRID(ST_MakePoint($8, $9), 4326), $10::jsonb, $11)
ON CONFLICT (id) DO NOTHING
"""


class SignalBus:
    def __init__(self, redis, pg_pool=None):
        self._redis = redis
        self._pg = pg_pool

    async def publish(self, signal: Signal) -> None:
        # 1) Live bus (the contract-defined transport).
        try:
            await self._redis.xadd(keys.STREAM_SIGNALS, signal.to_stream_fields())
        except Exception:
            log.exception("failed to XADD signal %s", signal.id)

        # 2) Durable evidence row (best-effort).
        if self._pg is None:
            return
        lat, lon = signal.position
        try:
            async with self._pg.acquire() as conn:
                await conn.execute(
                    _INSERT_SIGNAL,
                    signal.id,
                    signal.ts,
                    signal.type.value,
                    signal.mmsi,
                    signal.zone,
                    signal.severity,
                    signal.confidence,
                    lon,                 # ST_MakePoint(lon, lat)
                    lat,
                    json.dumps(signal.evidence),
                    signal.detector_version,
                )
        except Exception:
            log.warning("signal %s not persisted to Postgres", signal.id, exc_info=True)

    async def publish_many(self, signals: list[Signal]) -> None:
        for s in signals:
            await self.publish(s)
