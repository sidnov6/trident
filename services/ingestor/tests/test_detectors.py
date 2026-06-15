"""Unit tests for the ingestor's pure math and each detector firing.

No Redis or Postgres required: we drive detectors with an in-memory stub state
engine and craft the minimal track/state needed to make each one fire (and a
negative case to make sure it stays quiet otherwise).
"""
from __future__ import annotations

import asyncio
from collections import deque

import pytest

from ingestor.geoutil import (
    angular_diff,
    haversine_nm,
    implied_speed_kn,
    point_in_geojson,
    point_in_ring,
)
from trident_contracts import SignalType, VesselState


# --------------------------------------------------------------------------
# In-memory stub of VesselStateEngine's read surface (what detectors touch).
# --------------------------------------------------------------------------
class StubState:
    def __init__(self):
        self._states: dict[int, VesselState] = {}
        self._tracks: dict[int, deque] = {}

    def set(self, st: VesselState):
        self._states[st.mmsi] = st

    def push(self, mmsi, ts, lat, lon):
        self._tracks.setdefault(mmsi, deque(maxlen=256)).append((ts, lat, lon))

    def get_state(self, mmsi):
        return self._states.get(mmsi)

    def get_track(self, mmsi):
        return list(self._tracks.get(mmsi, ()))

    def all_states(self):
        return self._states.values()

    async def zone_count(self, zone):
        return sum(1 for s in self._states.values() if s.zone == zone)


def ctx(state, now):
    from ingestor.detectors.base import DetectorContext
    return DetectorContext(state=state, redis=None, now=now)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------
# Pure geometry / math
# --------------------------------------------------------------------------
def test_haversine_known_distance():
    # 1 degree of latitude ~ 60 nm.
    d = haversine_nm(0.0, 0.0, 1.0, 0.0)
    assert 59.0 < d < 61.0


def test_haversine_zero():
    assert haversine_nm(30.0, 32.0, 30.0, 32.0) == pytest.approx(0.0, abs=1e-6)


def test_implied_speed():
    # 60 nm in 1 hour = 60 kn.
    s = implied_speed_kn(0.0, 0.0, 0.0, 1.0, 0.0, 3600.0)
    assert 59.0 < s < 61.0
    # same/earlier timestamp -> 0 (handled as identity-conflict elsewhere).
    assert implied_speed_kn(0, 0, 100.0, 1, 0, 100.0) == 0.0


def test_angular_diff_wraps():
    assert angular_diff(10, 350) == pytest.approx(20.0)
    assert angular_diff(0, 180) == pytest.approx(180.0)
    assert angular_diff(90, 270) == pytest.approx(180.0)


def test_point_in_ring_square():
    sq = [[0, 0], [0, 10], [10, 10], [10, 0]]   # [lon, lat]
    assert point_in_ring(5, 5, sq) is True        # (lat, lon)
    assert point_in_ring(20, 20, sq) is False


def test_point_in_geojson_exclusion_zone():
    from trident_geo import load_zone_geojson
    fc = load_zone_geojson("suez_exclusion.geojson")
    # A point far away must be outside every exclusion polygon.
    assert point_in_geojson(0.0, 0.0, fc) is False


# --------------------------------------------------------------------------
# Spoofing: POSITION_JUMP + IDENTITY_CONFLICT
# --------------------------------------------------------------------------
def test_position_jump_fires():
    from ingestor.detectors.spoofing import SpoofingDetector
    det = SpoofingDetector()
    s = StubState()

    s.set(VesselState(mmsi=1, lat=30.0, lon=32.0, last_fix_ts=1000.0, zone="suez"))
    assert run(det.on_update(ctx(s, 1000.0), 1)) == []   # first fix, no prior

    # Teleport ~120 nm in 60 seconds -> implied speed ~7200 kn.
    s.set(VesselState(mmsi=1, lat=32.0, lon=32.0, last_fix_ts=1060.0, zone="suez"))
    sigs = run(det.on_update(ctx(s, 1060.0), 1))
    assert len(sigs) == 1
    assert sigs[0].type == SignalType.POSITION_JUMP
    assert sigs[0].evidence["implied_speed_kn"] > 40


def test_identity_conflict_fires():
    from ingestor.detectors.spoofing import SpoofingDetector
    det = SpoofingDetector()
    s = StubState()
    s.set(VesselState(mmsi=2, lat=30.0, lon=32.0, last_fix_ts=500.0, zone="suez"))
    run(det.on_update(ctx(s, 500.0), 2))
    # Same timestamp, different position.
    s.set(VesselState(mmsi=2, lat=30.5, lon=32.0, last_fix_ts=500.0, zone="suez"))
    sigs = run(det.on_update(ctx(s, 500.0), 2))
    assert len(sigs) == 1
    assert sigs[0].type == SignalType.IDENTITY_CONFLICT


# --------------------------------------------------------------------------
# Dark vessel: went_dark (tick) + reappearance (update)
# --------------------------------------------------------------------------
def test_dark_vessel_goes_dark_and_reappears():
    from ingestor.detectors.dark_vessel import DarkVesselDetector
    det = DarkVesselDetector()
    s = StubState()

    # Moving, in the Suez core (not near edge). Suez gap threshold = 900s.
    core_lat, core_lon = 30.05, 32.45
    s.set(VesselState(mmsi=636092123, lat=core_lat, lon=core_lon, sog=11.0,
                      last_fix_ts=0.0, zone="suez", flag="Liberia"))
    assert run(det.on_update(ctx(s, 0.0), 636092123)) == []   # just records last-seen

    # No further updates; tick well past the gap threshold -> went_dark fires.
    sigs = run(det.on_tick(ctx(s, 1000.0)))
    dark = [x for x in sigs if x.mmsi == 636092123]
    assert len(dark) == 1
    assert dark[0].type == SignalType.DARK_VESSEL
    assert dark[0].evidence["phase"] == "went_dark"

    # Reappears ~20 nm south after the blackout -> higher-severity reappearance.
    s.set(VesselState(mmsi=636092123, lat=core_lat - 0.34, lon=core_lon, sog=11.0,
                      last_fix_ts=1100.0, zone="suez", flag="Liberia"))
    sigs2 = run(det.on_update(ctx(s, 1100.0), 636092123))
    assert len(sigs2) == 1
    assert sigs2[0].type == SignalType.DARK_VESSEL
    assert sigs2[0].evidence["phase"] == "reappearance"
    assert sigs2[0].evidence["displacement_nm"] > 5
    assert sigs2[0].severity > dark[0].severity


def test_dark_vessel_quiet_near_edge():
    from ingestor.detectors.dark_vessel import DarkVesselDetector
    det = DarkVesselDetector()
    s = StubState()
    # Near the Suez bbox edge (sw_lat=29.85) -> "left coverage", must NOT fire.
    s.set(VesselState(mmsi=5, lat=29.86, lon=32.45, sog=11.0,
                      last_fix_ts=0.0, zone="suez"))
    run(det.on_update(ctx(s, 0.0), 5))
    assert run(det.on_tick(ctx(s, 5000.0))) == []


# --------------------------------------------------------------------------
# Loitering
# --------------------------------------------------------------------------
def test_loitering_fires_outside_anchorage():
    from ingestor.detectors.loitering import LoiteringDetector
    det = LoiteringDetector()
    s = StubState()
    mmsi = 636092456
    # A spot in the Gulf of Suez core, NOT in a designated anchorage.
    lat, lon = 30.05, 32.50
    s.set(VesselState(mmsi=mmsi, lat=lat, lon=lon, sog=0.4, zone="suez",
                      last_fix_ts=3600.0))
    # 60 minutes of near-stationary fixes within <2nm.
    for i in range(13):
        ts = i * 300.0       # every 5 min over an hour
        s.push(mmsi, ts, lat + 0.001 * (i % 2), lon + 0.001 * ((i + 1) % 2))
    sigs = run(det.on_update(ctx(s, 3600.0), mmsi))
    assert len(sigs) == 1
    assert sigs[0].type == SignalType.LOITERING


def test_loitering_quiet_when_moving():
    from ingestor.detectors.loitering import LoiteringDetector
    det = LoiteringDetector()
    s = StubState()
    mmsi = 7
    s.set(VesselState(mmsi=mmsi, lat=30.0, lon=32.5, sog=10.0, zone="suez",
                      last_fix_ts=3600.0))
    # Moving steadily north — large displacement across the window.
    for i in range(13):
        s.push(mmsi, i * 300.0, 30.0 + i * 0.05, 32.5)
    assert run(det.on_update(ctx(s, 3600.0), mmsi)) == []


# --------------------------------------------------------------------------
# Geofence breach
# --------------------------------------------------------------------------
def _interior_exclusion_point():
    """Find a point that is strictly inside an exclusion polygon (grid search)."""
    from ingestor.geoutil import point_in_geojson
    from trident_geo import load_zone_geojson
    fc = load_zone_geojson("suez_exclusion.geojson")
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    lo0, lo1 = min(lons), max(lons)
    la0, la1 = min(lats), max(lats)
    for i in range(1, 20):
        for j in range(1, 20):
            lon = lo0 + (lo1 - lo0) * i / 20
            lat = la0 + (la1 - la0) * j / 20
            if point_in_geojson(lat, lon, fc):
                return lat, lon
    raise AssertionError("no interior point found")


def test_geofence_breach_fires_on_grounding_dwell():
    from ingestor.detectors.geofence import GeofenceDetector, DWELL_FIXES
    det = GeofenceDetector()
    s = StubState()
    lat, lon = _interior_exclusion_point()
    # A grounded vessel: stuck in the strip, near-stationary, across several fixes.
    all_sigs = []
    for k in range(DWELL_FIXES + 1):
        s.set(VesselState(mmsi=9, lat=lat + 1e-4 * k, lon=lon, sog=0.2,
                          zone="suez", last_fix_ts=10.0 + k))
        all_sigs += run(det.on_update(ctx(s, 10.0 + k), 9))
    assert len(all_sigs) == 1                       # fires once, then suppressed
    assert all_sigs[0].type == SignalType.GEOFENCE_BREACH


def test_geofence_quiet_on_fast_transit():
    from ingestor.detectors.geofence import GeofenceDetector, DWELL_FIXES
    det = GeofenceDetector()
    s = StubState()
    lat, lon = _interior_exclusion_point()
    # A vessel sailing THROUGH the strip: each fix moves it well past 0.5 nm.
    fired = []
    for k in range(DWELL_FIXES + 2):
        s.set(VesselState(mmsi=10, lat=lat + 0.05 * k, lon=lon, sog=12.0,
                          zone="suez", last_fix_ts=10.0 + k))
        fired += run(det.on_update(ctx(s, 10.0 + k), 10))
    assert fired == []   # moving through, never a confirmed grounding


# --------------------------------------------------------------------------
# Reroute / U-turn
# --------------------------------------------------------------------------
def test_reroute_fires_on_course_reversal():
    from ingestor.detectors.reroute import RerouteDetector
    det = RerouteDetector()
    s = StubState()
    mmsi = 11
    s.set(VesselState(mmsi=mmsi, lat=30.5, lon=32.34, sog=10.0, zone="suez",
                      last_fix_ts=600.0))
    # Heading north (lat increasing) then reversing south (lat decreasing).
    pts = [
        (0.0, 30.40, 32.34), (60.0, 30.45, 32.34), (120.0, 30.50, 32.34),
        (180.0, 30.48, 32.34), (240.0, 30.43, 32.34), (300.0, 30.38, 32.34),
    ]
    for ts, lat, lon in pts:
        s.push(mmsi, ts, lat, lon)
    sigs = run(det.on_update(ctx(s, 600.0), mmsi))
    assert len(sigs) == 1
    assert sigs[0].type == SignalType.REROUTE
    assert sigs[0].evidence["reversal_deg"] > 150


def test_uturn_cluster_fires():
    from ingestor.detectors.reroute import RerouteDetector
    det = RerouteDetector()
    s = StubState()
    # Three vessels each execute a reversal -> per-vessel REROUTE, then cluster.
    for mmsi in (101, 102, 103):
        s.set(VesselState(mmsi=mmsi, lat=30.5, lon=32.34, sog=10.0, zone="suez",
                          last_fix_ts=600.0))
        pts = [
            (0.0, 30.40, 32.34), (60.0, 30.45, 32.34), (120.0, 30.50, 32.34),
            (180.0, 30.48, 32.34), (240.0, 30.43, 32.34), (300.0, 30.38, 32.34),
        ]
        for ts, lat, lon in pts:
            s.push(mmsi, ts, lat, lon)
        run(det.on_update(ctx(s, 600.0), mmsi))
    cluster = run(det.on_tick(ctx(s, 650.0)))
    uturns = [x for x in cluster if x.type == SignalType.UTURN]
    assert len(uturns) == 1
    assert uturns[0].mmsi == 0
    assert uturns[0].evidence["uturn_count"] == 3


# --------------------------------------------------------------------------
# Congestion (z-score over EWMA baseline)
# --------------------------------------------------------------------------
def test_congestion_fires_on_spike():
    from ingestor.detectors.congestion import CongestionDetector
    det = CongestionDetector()

    class CountState(StubState):
        def __init__(self, count):
            super().__init__()
            self._count = count

        async def zone_count(self, zone):
            return self._count if zone == "suez" else 0

    # Warm the baseline with a steady ~5 vessels, small variance.
    fired = []
    for _ in range(40):
        s = CountState(5)
        run(det.on_tick(ctx(s, 100.0)))
    # Now spike to 30 -> z well above 3.
    s = CountState(30)
    sigs = run(det.on_tick(ctx(s, 200.0)))
    cong = [x for x in sigs if x.type == SignalType.CONGESTION and x.zone == "suez"]
    assert len(cong) == 1
    assert cong[0].evidence["z_score"] > 3
