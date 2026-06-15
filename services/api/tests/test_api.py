"""API gateway shape tests.

Drives the FastAPI app with a TestClient against a fakeredis instance and a
stubbed asyncpg pool, asserting the REST surface returns shapes that match the
frozen contracts (VesselLite, ThreatLevel, /health posture).

These need ``fastapi``, ``httpx`` and ``fakeredis``, which may not be installed
in the lightweight validation environment. The module is import-guarded so a
missing dependency SKIPS rather than errors; it runs for real in CI / docker.
Pure-python pieces (the threat formula, the lite projection) are also tested
directly and do not need any of those deps.
"""
from __future__ import annotations

import time

import pytest

# --- pure-python: threat formula + lite projection (no heavy deps) ----------
from api.threat import HALF_LIFE_S, decayed_score, threat_for_zone
from trident_contracts.enums import ThreatLevel
from trident_contracts.vessel import VesselState
from api.state_reader import status_bits, to_lite


def test_threat_empty_is_green():
    assert threat_for_zone([]) is ThreatLevel.GREEN


def test_threat_decaying_max_buckets():
    now = 1_000_000.0
    # A single fresh, very-alarming firing -> CRITICAL.
    assert threat_for_zone([(now, 0.95)], now=now) is ThreatLevel.CRITICAL
    # The same firing one half-life old halves -> 0.475 -> ELEVATED.
    assert (
        threat_for_zone([(now - HALF_LIFE_S, 0.95)], now=now) is ThreatLevel.ELEVATED
    )
    # A swarm of low-severity firings must NOT sum into a high posture.
    swarm = [(now, 0.2) for _ in range(50)]
    assert threat_for_zone(swarm, now=now) is ThreatLevel.GREEN


def test_decayed_score_is_bounded():
    now = time.time()
    s = decayed_score([(now, 1.0), (now, 0.5)], now=now)
    assert 0.0 <= s <= 1.0
    assert s == pytest.approx(1.0, abs=1e-6)


def test_lite_projection_and_status_bits():
    now = 2_000_000.0
    st = VesselState(
        mmsi=636091234, lat=30.5, lon=32.4, sog=0.1, cog=180.0,
        ship_type=80, last_fix_ts=now - 5.0, zone="suez",
    )
    bits = status_bits(st, now=now, watchlist={636091234})
    # Loitering (sog<0.5 in a zone) + watchlist; NOT dark (fix is fresh).
    from trident_contracts.enums import (
        STATUS_BIT_DARK, STATUS_BIT_LOITERING, STATUS_BIT_WATCHLIST,
    )
    assert bits & STATUS_BIT_LOITERING
    assert bits & STATUS_BIT_WATCHLIST
    assert not (bits & STATUS_BIT_DARK)

    lite = to_lite(st, now=now, watchlist=set())
    assert lite.m == 636091234
    assert lite.t == 1            # ship_type 80 -> TANKER bucket
    assert lite.la == 30.5 and lite.lo == 32.4


# --- integration: TestClient over fakeredis + stub pool ---------------------
fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
fakeredis = pytest.importorskip("fakeredis")

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402
from api.state_reader import StateReader  # noqa: E402
from api.ws import StreamFanout  # noqa: E402
from trident_common import keys  # noqa: E402


class _StubAcquire:
    """Async-context wrapper around a stub connection."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _StubConn:
    async def fetch(self, *args, **kwargs):
        return []

    async def fetchrow(self, *args, **kwargs):
        return None

    async def execute(self, *args, **kwargs):
        return None


class _StubPool:
    """Minimal asyncpg-pool stand-in returning empty result sets."""

    def acquire(self):
        return _StubAcquire(_StubConn())

    async def close(self):
        return None


@pytest.fixture
def client():
    """A TestClient with fakeredis hot-state + a stub Postgres pool.

    We seed two vessels in Suez (one a tanker on the watchlist) and wire the
    app.state handles directly, bypassing the real lifespan datastore open.
    """
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)

    now = time.time()
    import asyncio

    async def _seed():
        # Two live vessels in the suez GEO index + their hashes.
        for mmsi, lat, lon, st in [
            (636091234, 30.55, 32.35, 80),
            (255801111, 30.60, 32.40, 70),
        ]:
            await r.hset(
                keys.vessel_key(mmsi),
                mapping={
                    "mmsi": mmsi, "lat": lat, "lon": lon, "sog": 8.0, "cog": 90.0,
                    "ship_type": st, "last_fix_ts": now, "first_seen_ts": now - 60,
                    "zone": "suez", "nav_status": 0,
                },
            )
            await r.geoadd(keys.zone_geo_key("suez"), (lon, lat, str(mmsi)))
        await r.sadd(keys.WATCHLIST_PRIORITY, "636091234")

    asyncio.get_event_loop().run_until_complete(_seed())

    app.state.redis = r
    app.state.pool = _StubPool()
    app.state.reader = StateReader(r)
    app.state.fanout = StreamFanout(None)  # no live tailers needed for REST tests

    # Use the app WITHOUT triggering lifespan (handles are pre-wired above).
    with TestClient(app, raise_server_exceptions=True) as _c:  # noqa: F841
        # TestClient(...) as ctx would run lifespan and clobber our handles;
        # instead build a bare client.
        pass
    return TestClient(app)


def test_health_shape(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "trident-api"
    assert body["feed"]["status"] in {"green", "amber", "red"}
    assert "redis" in body and "postgres" in body
    assert body["streams"]["signals"] == keys.STREAM_SIGNALS


def test_zones_shape(client):
    resp = client.get("/zones")
    assert resp.status_code == 200
    zones = resp.json()
    assert isinstance(zones, list) and zones
    ids = {z["id"] for z in zones}
    assert "suez" in ids
    suez = next(z for z in zones if z["id"] == "suez")
    assert suez["count"] >= 2          # the two seeded vessels
    assert suez["threat_level"] in {e.value for e in ThreatLevel}
    assert len(suez["center"]) == 2


def test_vessels_shape(client):
    resp = client.get("/vessels?zone=suez")
    assert resp.status_code == 200
    vessels = resp.json()
    assert isinstance(vessels, list) and len(vessels) == 2
    # Match VesselLite terse field names exactly.
    v = vessels[0]
    assert set(v.keys()) == {"m", "la", "lo", "s", "c", "t", "f", "st"}
    # The tanker on the watchlist must carry the WATCHLIST status bit.
    from trident_contracts.enums import STATUS_BIT_WATCHLIST
    tanker = next(x for x in vessels if x["m"] == 636091234)
    assert tanker["t"] == 1
    assert tanker["st"] & STATUS_BIT_WATCHLIST
