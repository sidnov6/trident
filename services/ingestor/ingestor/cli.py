"""Small operator CLI for the ingestor.

    python -m ingestor.cli counts

Prints live per-zone vessel counts every 2 seconds by reading the Redis GEO
indexes the state engine maintains. This is the M1 proof: with the ingestor
running (synthetic or live), counts should climb and hold for the active zone.
"""
from __future__ import annotations

import asyncio
import sys

from trident_common import get_settings, keys
from trident_geo import CHOKEPOINTS


async def _counts() -> None:
    settings = get_settings()
    import redis.asyncio as aioredis

    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await r.ping()
    except Exception as exc:  # pragma: no cover
        print(f"cannot reach Redis at {settings.redis_url}: {exc}", file=sys.stderr)
        return

    try:
        while True:
            parts = []
            total = 0
            for cp in CHOKEPOINTS:
                try:
                    n = int(await r.zcard(keys.zone_geo_key(cp.id)))
                except Exception:
                    n = 0
                total += n
                parts.append(f"{cp.id}={n}")
            print(f"[{_clock()}] total={total}  " + "  ".join(parts), flush=True)
            await asyncio.sleep(2.0)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await r.aclose()


def _clock() -> str:
    import time
    return time.strftime("%H:%M:%S")


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "counts"
    if cmd == "counts":
        try:
            asyncio.run(_counts())
        except KeyboardInterrupt:
            pass
        return 0
    print(f"unknown command: {cmd}\nusage: python -m ingestor.cli counts", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
