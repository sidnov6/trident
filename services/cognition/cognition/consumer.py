"""The Redis-stream consumer loop.

The resident brain. Reads ``keys.STREAM_SIGNALS`` with the cognition consumer
group (``XREADGROUP``, creating the stream + group with MKSTREAM if absent), and
for each Signal invokes the durable graph. The graph is invoked with a thread_id
keyed by (zone or mmsi) so the Sentinel's episodic correlation has continuity
across signals and survives restarts via the Postgres checkpointer.

ACK happens only after the graph run returns, so a crash mid-investigation leaves
the message pending for redelivery rather than silently lost.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

try:  # redis-py raises this from a blocking read that times out idle
    from redis.exceptions import TimeoutError as RedisTimeoutError
except Exception:  # pragma: no cover - redis always present at runtime
    class RedisTimeoutError(Exception):
        ...

from trident_common import keys
from trident_contracts.signal import Signal

log = logging.getLogger("cognition.consumer")

CONSUMER_NAME = "cognition-1"
_BLOCK_MS = 5000
_BATCH = 16


async def ensure_group(redis: Any) -> None:
    """Create the consumer group (and stream) if it does not already exist."""
    try:
        await redis.xgroup_create(
            name=keys.STREAM_SIGNALS,
            groupname=keys.CONSUMER_GROUP_COGNITION,
            id="0",
            mkstream=True,
        )
        log.info("Created consumer group %s on %s",
                 keys.CONSUMER_GROUP_COGNITION, keys.STREAM_SIGNALS)
    except Exception as exc:  # BUSYGROUP if it already exists — benign
        if "BUSYGROUP" in str(exc):
            log.info("Consumer group %s already exists.", keys.CONSUMER_GROUP_COGNITION)
        else:  # pragma: no cover
            raise


def thread_id_for(signal: Signal) -> str:
    """Checkpoint thread key. Zone gives the Sentinel its per-zone episodic
    window; fall back to MMSI when a signal carries no zone."""
    return f"zone:{signal.zone}" if signal.zone else f"mmsi:{signal.mmsi}"


async def process_one(graph: Any, signal: Signal) -> None:
    """Invoke the durable graph for a single signal under its thread_id."""
    config = {"configurable": {"thread_id": thread_id_for(signal)}}
    await graph.ainvoke({"signal": signal.model_dump(mode="json")}, config=config)


async def run_consumer(
    redis: Any,
    graph: Any,
    *,
    stop: Optional[asyncio.Event] = None,
) -> None:
    """The main consume loop. Runs until ``stop`` is set (or forever)."""
    await ensure_group(redis)
    log.info("Cognition consumer online — listening on %s", keys.STREAM_SIGNALS)

    while stop is None or not stop.is_set():
        try:
            resp = await redis.xreadgroup(
                groupname=keys.CONSUMER_GROUP_COGNITION,
                consumername=CONSUMER_NAME,
                streams={keys.STREAM_SIGNALS: ">"},
                count=_BATCH,
                block=_BLOCK_MS,
            )
        except RedisTimeoutError:
            # A blocking read that returned no new signal in _BLOCK_MS — the normal
            # idle case under low traffic, not an error. Loop straight back.
            continue
        except Exception as exc:  # pragma: no cover - transient redis errors
            log.warning("XREADGROUP failed (%s); retrying shortly.", exc)
            await asyncio.sleep(1.0)
            continue

        if not resp:
            continue

        for _stream, messages in resp:
            for msg_id, fields in messages:
                try:
                    signal = Signal.from_stream_fields(fields)
                    await process_one(graph, signal)
                except Exception as exc:  # never let one bad message kill the loop
                    log.exception("Failed processing message %s: %s", msg_id, exc)
                finally:
                    # ACK regardless: a poison message must not wedge the group.
                    # The full payload is durably in the signals table anyway.
                    try:
                        await redis.xack(
                            keys.STREAM_SIGNALS, keys.CONSUMER_GROUP_COGNITION, msg_id
                        )
                    except Exception:  # pragma: no cover
                        log.warning("XACK failed for %s", msg_id)
