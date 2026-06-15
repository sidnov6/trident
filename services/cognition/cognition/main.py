"""Cognition service entrypoint.

Boots the resident brain:
  1. open the datastore handles (Redis, asyncpg pool, Postgres checkpointer),
  2. build the durable LangGraph,
  3. run the signal-stream consumer loop until shutdown.

Every external dependency degrades gracefully. If Postgres is unreachable the
graph runs without a checkpointer (no durability, but it still runs); if Redis is
unreachable we cannot consume and exit with a clear error. The LLM is already
optional (see ``cognition.llm``).
"""
from __future__ import annotations

import asyncio
import logging
import signal as os_signal
from contextlib import AsyncExitStack
from typing import Any, Optional

from trident_common.settings import get_settings

from .consumer import run_consumer
from .graph import Deps, build_graph
from .llm import has_llm, model_name

log = logging.getLogger("cognition.main")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )


async def _open_redis(url: str) -> Optional[Any]:
    try:
        import redis.asyncio as aioredis
    except ImportError:  # pragma: no cover
        log.error("redis package not installed — cannot consume the signal stream.")
        return None
    client = aioredis.from_url(url, decode_responses=True)
    await client.ping()
    log.info("Connected to Redis at %s", url)
    return client


async def _open_pool(dsn: str) -> Optional[Any]:
    try:
        import asyncpg
    except ImportError:  # pragma: no cover
        log.warning("asyncpg not installed — running without Postgres persistence.")
        return None
    try:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
        log.info("Connected to Postgres pool.")
        return pool
    except Exception as exc:
        log.warning("Postgres unavailable (%s) — running without persistence.", exc)
        return None


async def _open_checkpointer(stack: AsyncExitStack, dsn: str) -> Optional[Any]:
    """The durable Postgres checkpointer (AsyncPostgresSaver). None if the
    dependency or DB is unavailable — the graph still compiles and runs."""
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError:  # pragma: no cover
        log.warning("langgraph-checkpoint-postgres not installed — no durable checkpointing.")
        return None
    try:
        cp = await stack.enter_async_context(AsyncPostgresSaver.from_conn_string(dsn))
        await cp.setup()   # idempotent: creates the checkpoint tables if absent
        log.info("Postgres checkpointer ready (durable graph state).")
        return cp
    except Exception as exc:
        log.warning("Checkpointer unavailable (%s) — graph runs without durability.", exc)
        return None


async def main() -> None:
    settings = get_settings()
    _configure_logging(settings.log_level)

    log.info("TRIDENT cognition starting. LLM=%s (model=%s).",
             "groq" if has_llm() else "deterministic-fallback", model_name())

    async with AsyncExitStack() as stack:
        redis = await _open_redis(settings.redis_url)
        if redis is None:
            raise SystemExit("Redis is required to consume the signal stream.")
        stack.push_async_callback(redis.aclose)

        pool = await _open_pool(settings.database_url)
        if pool is not None:
            stack.push_async_callback(pool.close)

        checkpointer = await _open_checkpointer(stack, settings.database_url)

        deps = Deps(pool=pool, redis=redis)
        graph = build_graph(deps, checkpointer=checkpointer)

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (os_signal.SIGINT, os_signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # pragma: no cover - non-unix
                pass

        await run_consumer(redis, graph, stop=stop)
        log.info("Cognition shutting down cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
