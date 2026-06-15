r"""Ingestor entrypoint — wires the Tier 0-2 pipeline together.

Pipeline (per message, all cheap compute):
    source -> normalize -> state.apply -> run detectors -> publish signals
                                       \-> enqueue track + vessel (async writer)

Plus two background tasks decoupled from the hot path:
    * TrackWriter.run() — batches fixes/vessels into Postgres.
    * detector tick loop — runs zone-level + staleness detectors on a slow cadence.

Source selection is by settings.ais_source ("synthetic" default, "live" =
AISStream). Graceful shutdown on SIGINT/SIGTERM drains the writer and closes the
feed.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from typing import AsyncIterator, Optional

from trident_common import get_settings
from trident_geo import validate_boxes

from .bus import SignalBus
from .client import AISStreamClient
from .detectors import DetectorContext, build_detectors
from .feedgap import FeedGapRecorder
from .normalize import normalize
from .state import VesselStateEngine
from .synthetic import SyntheticAISSource
from .writer import TrackWriter

log = logging.getLogger("ingestor.main")

DETECTOR_TICK_S = 2.0   # cadence for zone-level + dark-staleness detectors


class Ingestor:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._redis = None
        self._pg = None
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    async def _build_pools(self):
        # Redis (required for the live bus + hot state).
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                self._settings.redis_url, decode_responses=True
            )
            await self._redis.ping()
            log.info("connected to Redis at %s", self._settings.redis_url)
        except Exception:
            log.warning("Redis unavailable — running with in-process state only", exc_info=True)
            self._redis = None

        # Postgres (durable tracks/signals/vessels/feed_gaps). Optional.
        try:
            import asyncpg
            self._pg = await asyncpg.create_pool(
                self._settings.database_url, min_size=1, max_size=5
            )
            log.info("connected to Postgres")
        except Exception:
            log.warning("Postgres unavailable — persistence disabled", exc_info=True)
            self._pg = None

    def _make_source(self, feedgap) -> AsyncIterator[dict]:
        if self._settings.ais_source == "live":
            log.info("AIS source: LIVE (AISStream)")
            return AISStreamClient(self._settings.aisstream_api_key, feedgap=feedgap)
        log.info("AIS source: SYNTHETIC (offline Suez scenario)")
        return SyntheticAISSource(feedgap=feedgap)

    async def run(self) -> None:
        # Fail fast on the [lat, lon] inversion bug before touching the network.
        validate_boxes()
        log.info("bounding boxes validated")

        await self._build_pools()

        state = VesselStateEngine(self._redis)
        bus = SignalBus(self._redis, self._pg)
        writer = TrackWriter(self._pg)
        feedgap = FeedGapRecorder(self._pg)
        detectors = build_detectors()

        source = self._make_source(feedgap)

        # Background tasks.
        self._tasks.append(asyncio.create_task(writer.run(), name="track-writer"))
        self._tasks.append(
            asyncio.create_task(self._tick_loop(state, bus, detectors), name="detector-tick")
        )

        # Main ingest loop.
        ingest = asyncio.create_task(
            self._ingest_loop(source, state, bus, writer, detectors), name="ingest"
        )
        self._tasks.append(ingest)

        await self._stop.wait()
        log.info("shutdown requested — draining")

        # Graceful shutdown.
        if hasattr(source, "close"):
            await source.close()
        writer.stop()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._pg is not None:
            await self._pg.close()
        if self._redis is not None:
            await self._redis.aclose()
        log.info("ingestor stopped cleanly")

    async def _ingest_loop(self, source, state, bus, writer, detectors) -> None:
        try:
            async for envelope in source:
                upd = normalize(envelope)
                if upd is None:
                    continue
                vstate = await state.apply(upd)

                # Persistence is fire-and-forget onto bounded queues.
                if not upd.is_static and upd.lat is not None:
                    writer.enqueue_fix(vstate)
                if upd.is_static:
                    writer.enqueue_vessel(vstate)

                # Per-vessel detectors run on the fix's own timestamp so synthetic
                # time-compression and live wall-clock both behave correctly.
                ctx = DetectorContext(state=state, redis=self._redis, now=upd.ts)
                for det in detectors:
                    try:
                        sigs = await det.on_update(ctx, upd.mmsi)
                    except Exception:
                        log.exception("detector %s on_update failed", det.name)
                        continue
                    if sigs:
                        await bus.publish_many(sigs)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("ingest loop crashed")
            self._stop.set()

    async def _tick_loop(self, state, bus, detectors) -> None:
        """Slow cadence for zone-level + dark-staleness detectors."""
        import time
        try:
            while True:
                await asyncio.sleep(DETECTOR_TICK_S)
                ctx = DetectorContext(state=state, redis=self._redis, now=time.time())
                for det in detectors:
                    try:
                        sigs = await det.on_tick(ctx)
                    except Exception:
                        log.exception("detector %s on_tick failed", det.name)
                        continue
                    if sigs:
                        await bus.publish_many(sigs)
        except asyncio.CancelledError:
            raise

    def request_stop(self, *_a) -> None:
        self._stop.set()


async def _amain() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    ingestor = Ingestor()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, ingestor.request_stop)
        except NotImplementedError:
            pass   # e.g. Windows
    await ingestor.run()


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
