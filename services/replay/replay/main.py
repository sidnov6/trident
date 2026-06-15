"""Replay service entrypoint — the FastAPI app on :8100.

Lifespan opens the asyncpg pool (the only datastore replay needs — it reads the
``tracks`` hypertable). CORS is open for the local web client. Routes (track REST,
proximity REST, replay WebSocket) are mounted here.

Postgres is optional at boot: the app starts even if the DB is down and serves
empty results, so the service never hard-fails on a dependency hiccup.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from trident_common.settings import get_settings

from .routes import router

log = logging.getLogger("replay.main")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )


async def _open_pool(dsn: str) -> Optional[Any]:
    try:
        import asyncpg
    except ImportError:  # pragma: no cover
        log.warning("asyncpg not installed — replay will serve empty results.")
        return None
    try:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
        log.info("Connected to Postgres pool.")
        return pool
    except Exception as exc:
        log.warning("Postgres unavailable (%s) — replay serving empty results.", exc)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _configure_logging(settings.log_level)
    log.info("TRIDENT replay service starting.")

    app.state.pool = await _open_pool(settings.database_url)
    try:
        yield
    finally:
        log.info("TRIDENT replay service shutting down.")
        if app.state.pool is not None:
            await app.state.pool.close()


app = FastAPI(title="TRIDENT Replay", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "service": "trident-replay",
        "postgres": getattr(app.state, "pool", None) is not None,
    }
