"""API gateway entrypoint — the FastAPI app.

Lifespan opens the datastore handles (Redis + asyncpg pool) and the shared
stream fanout; shutdown tears them down. CORS is open for the local web client
(localhost:3000). REST routes + the ``/ws`` WebSocket are mounted here.

Every datastore is optional: the app boots and serves (degraded) even if Redis or
Postgres is down, so the gateway never hard-fails on a dependency hiccup. The
``/health`` endpoint reports the real feed posture so the UI can show it.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from trident_common import keys
from trident_common.settings import get_settings

from .routes import router
from .state_reader import StateReader
from .ws import StreamFanout, ws_router

log = logging.getLogger("api.main")

# Feed-health thresholds (messages/sec + last-fix age) -> green/amber/red.
HEALTH_MPS_GREEN = 1.0          # >= this many vessel fixes/sec => healthy
HEALTH_MPS_AMBER = 0.1          # below GREEN but >= this => degraded
HEALTH_FIX_AGE_GREEN_S = 30.0   # freshest fix younger than this => healthy
HEALTH_FIX_AGE_AMBER_S = 120.0  # below this => degraded, beyond => stale


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )


async def _open_redis(url: str) -> Optional[Any]:
    try:
        import redis.asyncio as aioredis
    except ImportError:  # pragma: no cover
        log.error("redis package not installed — vessel state unavailable.")
        return None
    try:
        client = aioredis.from_url(url, decode_responses=True)
        await client.ping()
        log.info("Connected to Redis at %s", url)
        return client
    except Exception as exc:
        log.warning("Redis unavailable (%s) — serving without hot-state.", exc)
        return None


async def _open_pool(dsn: str) -> Optional[Any]:
    try:
        import asyncpg
    except ImportError:  # pragma: no cover
        log.warning("asyncpg not installed — serving without Postgres.")
        return None
    try:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
        log.info("Connected to Postgres pool.")
        return pool
    except Exception as exc:
        log.warning("Postgres unavailable (%s) — serving without durable record.", exc)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _configure_logging(settings.log_level)
    log.info("TRIDENT api gateway starting.")

    redis = await _open_redis(settings.redis_url)
    pool = await _open_pool(settings.database_url)

    app.state.redis = redis
    app.state.pool = pool
    app.state.reader = StateReader(redis)
    app.state.fanout = StreamFanout(redis)
    await app.state.fanout.start()

    try:
        yield
    finally:
        log.info("TRIDENT api gateway shutting down.")
        await app.state.fanout.stop()
        if pool is not None:
            await pool.close()
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:  # pragma: no cover
                pass


app = FastAPI(title="TRIDENT API", version="1.0.0", lifespan=lifespan)

# Same-origin single-container deploys (e.g. the Hugging Face Space) serve the UI
# from this app, so allow any origin there; localhost dev keeps the tight default.
_cors_origins = ["*"] if os.environ.get("TRIDENT_STATIC_DIR") else ["http://localhost:3000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(ws_router)

# Optionally serve a pre-built static frontend (Next.js export) on the SAME port,
# so one container exposes UI + REST + WS through a single origin. Mounted LAST so
# the API routes and /ws above always take precedence over the catch-all.
_static_dir = os.environ.get("TRIDENT_STATIC_DIR")
if _static_dir and os.path.isdir(_static_dir):
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="ui")


async def _feed_health(redis: Optional[Any]) -> dict[str, Any]:
    """Derive feed posture from Redis: messages/sec + freshest last-fix age.

    We approximate throughput from the per-MMSI ``last_fix_ts`` spread across the
    live ``vessel:*`` set (no extra counter needed): count vessels whose freshest
    fix landed in the last second window, and take the youngest fix age overall.

    status: green  — healthy stream, fresh fixes
            amber  — degraded (slow or aging)
            red    — no Redis, no vessels, or stale beyond the amber bound
    """
    now = time.time()
    if redis is None:
        return {"status": "red", "reason": "no_redis", "mps": 0.0,
                "last_fix_age_s": None, "vessels": 0}

    youngest_age: Optional[float] = None
    recent_1s = 0
    total = 0
    try:
        async for key in redis.scan_iter(match="vessel:*", count=500):
            total += 1
            try:
                raw = await redis.hget(key, "last_fix_ts")
            except Exception:
                continue
            if raw is None:
                continue
            if isinstance(raw, bytes):
                raw = raw.decode()
            try:
                fix_ts = float(raw)
            except (TypeError, ValueError):
                continue
            age = now - fix_ts
            if youngest_age is None or age < youngest_age:
                youngest_age = age
            if age <= 1.0:
                recent_1s += 1
    except Exception:
        return {"status": "red", "reason": "scan_failed", "mps": 0.0,
                "last_fix_age_s": None, "vessels": 0}

    mps = float(recent_1s)   # fixes observed in the last ~1s window

    if total == 0:
        status = "red"
    elif (
        mps >= HEALTH_MPS_GREEN
        and youngest_age is not None
        and youngest_age <= HEALTH_FIX_AGE_GREEN_S
    ):
        status = "green"
    elif (
        mps >= HEALTH_MPS_AMBER
        or (youngest_age is not None and youngest_age <= HEALTH_FIX_AGE_AMBER_S)
    ):
        status = "amber"
    else:
        status = "red"

    return {
        "status": status,
        "mps": mps,
        "last_fix_age_s": youngest_age,
        "vessels": total,
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    """Feed-health probe (green/amber/red) + datastore reachability."""
    redis = getattr(app.state, "redis", None)
    pool = getattr(app.state, "pool", None)
    feed = await _feed_health(redis)
    return {
        "service": "trident-api",
        "feed": feed,
        "redis": redis is not None,
        "postgres": pool is not None,
        "streams": {
            "signals": keys.STREAM_SIGNALS,
            "incidents": keys.STREAM_INCIDENTS,
        },
    }
