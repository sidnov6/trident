"""FeedGapRecorder — disconnections are first-class intelligence.

When the AIS feed drops we open a `feed_gaps` row (ended_at NULL); when it
reconnects we close the most recent open row. A blackout in the feed is itself a
finding (someone may be exploiting the gap), so we persist it durably.

No-ops gracefully when there is no Postgres pool (synthetic/dev mode).
"""
from __future__ import annotations

import logging

log = logging.getLogger("ingestor.feedgap")

_OPEN_GAP = """
INSERT INTO feed_gaps (started_at, reason)
VALUES (to_timestamp($1), $2)
RETURNING id
"""

_CLOSE_GAP = """
UPDATE feed_gaps SET ended_at = to_timestamp($1)
WHERE id = $2 AND ended_at IS NULL
"""


class FeedGapRecorder:
    def __init__(self, pg_pool):
        self._pg = pg_pool
        self._open_id: int | None = None

    async def open(self, ts: float, reason: str = "ais_disconnect") -> None:
        """Record the start of a feed gap (idempotent: ignores a double-open)."""
        if self._open_id is not None:
            return
        if self._pg is None:
            self._open_id = -1   # sentinel so close() is symmetric in no-db mode
            log.info("feed gap opened (no-db): %s", reason)
            return
        try:
            async with self._pg.acquire() as conn:
                self._open_id = await conn.fetchval(_OPEN_GAP, ts, reason)
            log.info("feed gap opened id=%s reason=%s", self._open_id, reason)
        except Exception:
            log.warning("could not open feed_gap row", exc_info=True)

    async def close(self, ts: float) -> None:
        """Close the currently-open feed gap, if any."""
        if self._open_id is None:
            return
        gap_id, self._open_id = self._open_id, None
        if self._pg is None or gap_id == -1:
            log.info("feed gap closed (no-db)")
            return
        try:
            async with self._pg.acquire() as conn:
                await conn.execute(_CLOSE_GAP, ts, gap_id)
            log.info("feed gap closed id=%s", gap_id)
        except Exception:
            log.warning("could not close feed_gap row id=%s", gap_id, exc_info=True)
