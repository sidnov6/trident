"""Fleetscan entrypoint.

A standalone asyncio service: connect to Redis, then sweep the global vessel
state every SCAN_INTERVAL_S, classifying every ship with the deterministic fleet
agents and publishing deduped FleetAlerts to STREAM_FLEET_ALERTS. No DB, no LLM
(the optional Narrator is a separate concern). Degrades to idle if Redis is down.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import time
from typing import Any, Optional

from trident_common.settings import get_settings

from . import config as C
from .sweep import FleetSweep

log = logging.getLogger("fleetscan.main")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )


async def _open_redis(url: str) -> Optional[Any]:
    try:
        import redis.asyncio as aioredis
    except ImportError:  # pragma: no cover
        log.error("redis package not installed — fleetscan cannot run.")
        return None
    try:
        client = aioredis.from_url(url, decode_responses=True)
        await client.ping()
        log.info("Connected to Redis at %s", url)
        return client
    except Exception as exc:
        log.warning("Redis unavailable (%s) — fleetscan idle.", exc)
        return None


async def main() -> None:
    settings = get_settings()
    _configure_logging(settings.log_level)
    log.info("TRIDENT fleetscan starting — %d agents, %.0fs sweep cadence.",
             6, C.SCAN_INTERVAL_S)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover (Windows)
            pass

    redis = await _open_redis(settings.redis_url)
    if redis is None:
        # idle so the container stays up (matches the rest of the stack)
        await stop.wait()
        return

    sweep = FleetSweep(redis)
    log.info("Fleetscan online — sweeping %s every %.0fs.", "GLOBAL_GEO", C.SCAN_INTERVAL_S)

    while not stop.is_set():
        t0 = time.time()
        try:
            n = await sweep.run_once(t0)
            if n:
                log.info("sweep published %d alert(s) in %.2fs", n, time.time() - t0)
        except Exception:
            log.exception("sweep failed")
        # sleep the remainder of the cadence
        elapsed = time.time() - t0
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(1.0, C.SCAN_INTERVAL_S - elapsed))
        except asyncio.TimeoutError:
            pass

    log.info("fleetscan stopped cleanly")
    try:
        await redis.aclose()
    except Exception:  # pragma: no cover
        pass


def _amain() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    _amain()
