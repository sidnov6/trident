"""AISStreamClient — one persistent WebSocket to AISStream.

A single connection to wss://stream.aisstream.io/v0/stream, subscribed to the
chokepoint bounding boxes. Yields raw AIS envelope dicts as an async generator.

Resilience:
  * exponential backoff with jitter on reconnect (1 -> 2 -> 4 ... capped 30s),
    reset to the floor after a clean minute connected,
  * re-subscription debounced to at most once per second,
  * `Error` frames from AISStream are logged, not fatal,
  * on disconnect a feed_gap is opened; on (re)connect it is closed.

The generator never raises on a transient network fault — it loops and
reconnects forever until `close()` is called.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import AsyncIterator, Optional

import websockets

from trident_common.settings import get_settings
from trident_geo import CHOKEPOINT_BOXES

log = logging.getLogger("ingestor.client")

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"
BACKOFF_FLOOR_S = 1.0
BACKOFF_CAP_S = 30.0
CLEAN_RUN_RESET_S = 60.0       # connected this long -> reset backoff to floor
RESUB_DEBOUNCE_S = 1.0         # at most one subscription frame per second


class AISStreamClient:
    def __init__(self, api_key: str, feedgap=None):
        if not api_key:
            raise ValueError("AISStreamClient requires an AISSTREAM_API_KEY")
        self._api_key = api_key
        self._feedgap = feedgap
        self._closed = False
        self._last_subscribe = 0.0

    def _boxes(self) -> list:
        # Global coverage = one world-spanning box (all ships everywhere).
        # AISStream wants [[swLat, swLon], [neLat, neLon]].
        if get_settings().ais_global:
            return [[[-90.0, -180.0], [90.0, 180.0]]]
        return CHOKEPOINT_BOXES

    def _subscription_message(self) -> str:
        return json.dumps({
            "APIKey": self._api_key,
            "BoundingBoxes": self._boxes(),
            "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
        })

    async def _subscribe(self, ws) -> None:
        """Send the subscription frame, debounced to <=1/sec."""
        now = time.monotonic()
        wait = RESUB_DEBOUNCE_S - (now - self._last_subscribe)
        if wait > 0:
            await asyncio.sleep(wait)
        await ws.send(self._subscription_message())
        self._last_subscribe = time.monotonic()
        scope = "WORLD (global)" if get_settings().ais_global else f"{len(CHOKEPOINT_BOXES)} chokepoint boxes"
        log.info("subscribed to %s", scope)

    async def stream(self) -> AsyncIterator[dict]:
        backoff = BACKOFF_FLOOR_S
        while not self._closed:
            connected_at: Optional[float] = None
            try:
                async with websockets.connect(
                    AISSTREAM_URL, ping_interval=20, ping_timeout=20, max_size=2**22
                ) as ws:
                    connected_at = time.monotonic()
                    await self._subscribe(ws)
                    if self._feedgap is not None:
                        await self._feedgap.close(time.time())
                    backoff = BACKOFF_FLOOR_S   # provisional reset; see below

                    async for raw in ws:
                        # Reset backoff only after a sustained clean run.
                        if (
                            connected_at is not None
                            and time.monotonic() - connected_at >= CLEAN_RUN_RESET_S
                        ):
                            backoff = BACKOFF_FLOOR_S
                        msg = self._decode(raw)
                        if msg is None:
                            continue
                        if self._is_error(msg):
                            log.warning("AISStream Error frame: %s", msg.get("Error") or msg)
                            continue
                        yield msg
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("AIS websocket dropped: %s", exc)

            if self._closed:
                break

            # Disconnected -> record a feed gap and back off with jitter.
            if self._feedgap is not None:
                await self._feedgap.open(time.time(), reason="ais_disconnect")
            sleep_for = min(backoff, BACKOFF_CAP_S) * (0.5 + random.random())
            log.info("reconnecting in %.1fs", sleep_for)
            await asyncio.sleep(sleep_for)
            backoff = min(backoff * 2.0, BACKOFF_CAP_S)

    @staticmethod
    def _decode(raw) -> Optional[dict]:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", "replace")
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _is_error(msg: dict) -> bool:
        mt = msg.get("MessageType") or msg.get("message_type")
        return mt == "Error" or "Error" in msg

    async def close(self) -> None:
        self._closed = True

    # Make the client usable directly as `async for msg in client:`.
    def __aiter__(self) -> AsyncIterator[dict]:
        return self.stream()
