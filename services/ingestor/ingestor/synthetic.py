"""SyntheticAISSource — an offline Suez scenario that drives the whole pipeline.

Same async-generator interface as AISStreamClient (``async for msg in source``),
but needs no API key and emits messages in AISStream's exact JSON envelope shape,
so `normalize.py` is shared verbatim between live and synthetic feeds.

WHAT IT GENERATES
-----------------
~30 background vessels transiting the Suez fairway centreline (loaded from
``suez_fairway.geojson``, then nudged into the navigable channel so routine
transits don't trip GEOFENCE_BREACH) north<->south at 8-14 kn with believable
cog, each emitting a PositionReport every report interval plus one ShipStaticData
on first sight.

THE SCRIPTED DEMO ARC (deterministic — proves every detector offline)
---------------------------------------------------------------------
Times below are in *scenario seconds*. Wall-clock pacing is independent: with the
default ``report_interval_s=30`` and ``tick_s=0.25``, one scenario report = 0.25
wall-sec, so the 16-minute blackout passes in ~8 wall-sec and the whole arc —
including the 60-min loiter window — completes in well under a minute.

  t=0     : all vessels spawned along the fairway, transiting.
  t≈30s   : "PERSIA STAR" (MMSI 636092123, Liberia flag, ship_type 82 tanker)
            and "GULF LOITERER" (MMSI 636092456, Liberia tanker) are both in the
            Gulf of Suez core (~30.0N), close together.
  t≈30s.. : GULF LOITERER drops anchor and LOITERS (~0.6kn, <0.5nm orbit) near
            PERSIA STAR — the STS setup. -> LOITERING fires once its ring buffer
            spans the loiter window (~t≈1830s scenario / ~15 wall-sec).
  t≈40s   : PERSIA STAR goes DARK — stops emitting in the core (NOT near an
            edge, sog>0.5 at blackout). Once the Suez 15-min gap threshold
            elapses (~t≈940s scenario), the detector fires "went_dark".
  t=1000s : PERSIA STAR REAPPEARS ~24 nm south, having "transited dark" — fires
            a second, higher-severity DARK_VESSEL with gap_minutes (~16) +
            displacement_nm (~24) (the sanctions/STS reappearance signature);
            the implausible implied speed also trips a POSITION_JUMP.
  mid-arc : a U-TURN CLUSTER — three northbound vessels reverse course >150°
            within the canal in a short window -> per-vessel REROUTE x3 and a
            zone-level UTURN cluster (the Ever-Given convoy-reversal signature).

All randomness is seeded, so the arc is identical on every run.
"""
from __future__ import annotations

import asyncio
import math
import random
import time
from datetime import datetime, timezone
from typing import AsyncIterator

from trident_geo import load_zone_geojson

# --- scenario identities ---------------------------------------------------
DARK_MMSI = 636092123          # PERSIA STAR — Liberia (636), tanker (82)
LOITER_MMSI = 636092456        # GULF LOITERER — Liberia tanker, STS partner
UTURN_MMSIS = [351777001, 351777002, 351777003]   # Panama-flagged convoy

# scenario timeline (scenario-seconds)
T_LOITER_START = 30
T_DARK_START = 40
T_DARK_END = 40 + 16 * 60       # ~16 scenario-minutes dark
T_UTURN = 90


class SyntheticAISSource:
    """Deterministic Suez AIS generator.

    Each loop advances the scenario clock by `report_interval_s` (scenario
    seconds between successive AIS fixes for a vessel) and emits one fix per
    vessel, then sleeps `tick_s` wall-seconds. The two are decoupled on purpose:
    `report_interval_s` controls scenario *fidelity* (and must be small enough
    that a 60-min loiter window holds enough fixes in the 256-deep ring buffer),
    while `tick_s` controls how fast the demo plays in real time. The 16-minute
    blackout therefore passes in `(16*60 / report_interval_s) * tick_s` wall-sec.

    Parameters
    ----------
    n_background:
        number of plausible background transit vessels (~25-40).
    report_interval_s:
        scenario seconds between a vessel's successive fixes (default 30 -> a
        60-min window spans ~120 fixes, comfortably inside the ring buffer).
    tick_s:
        real wall-clock seconds slept per loop (demo pacing knob).
    seed:
        RNG seed — fixed so the arc is reproducible.
    """

    def __init__(
        self,
        n_background: int = 30,
        report_interval_s: float = 30.0,
        tick_s: float = 0.25,
        seed: int = 1337,
        feedgap=None,
    ) -> None:
        self._n = max(25, min(40, n_background))
        self._report_interval_s = report_interval_s
        self._tick_s = tick_s
        self._rng = random.Random(seed)
        self._feedgap = feedgap            # unused (synthetic never gaps); kept for parity
        self._closed = False

        self._exclusion = load_zone_geojson("suez_exclusion.geojson")
        # Densify, then shift any vertex inside an approximate bank-exclusion
        # strip eastward into the navigable channel, ONCE at load time. Densifying
        # first keeps the interpolated segments (not just vertices) in clear
        # water, so routine transits never trip GEOFENCE_BREACH (reserved for the
        # scripted grounding). Motion stays smooth + distance-correct.
        self._centerline = self._channel_corrected(
            self._densify(self._load_centerline(), step_deg=0.02)
        )
        self._track_nm = self._centerline_length_nm()
        self._vessels = self._spawn()

    # -- geometry ----------------------------------------------------------
    @staticmethod
    def _load_centerline() -> list[tuple[float, float]]:
        """Port Said -> Suez centreline as (lat, lon). The fairway polygon is a
        closed loop; the first half (north->south) is the usable centreline."""
        fc = load_zone_geojson("suez_fairway.geojson")
        ring = fc["features"][0]["geometry"]["coordinates"][0]  # [lon, lat] pairs
        half = ring[: len(ring) // 2 + 1]
        return [(lat, lon) for lon, lat in half]   # -> (lat, lon)

    @staticmethod
    def _densify(pts: list[tuple[float, float]], step_deg: float) -> list[tuple[float, float]]:
        """Subdivide a polyline so no segment is longer than ~step_deg, by linear
        interpolation. Keeps channel-correction faithful between sparse vertices."""
        out: list[tuple[float, float]] = []
        for a, b in zip(pts, pts[1:]):
            d = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
            n = max(1, int(d / step_deg))
            for k in range(n):
                t = k / n
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        out.append(pts[-1])
        return out

    def _channel_corrected(self, pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """Nudge each (lat, lon) vertex east until both it and a small eastward
        margin clear the bank-exclusion strips. The margin requirement means the
        straight segments interpolated between corrected vertices also stay in
        clear water, so routine transits never trip GEOFENCE_BREACH. Applied once
        to the centreline; the corrected track is a smooth polyline."""
        from .geoutil import point_in_geojson
        out = []
        for lat, lon in pts:
            for _ in range(120):
                # Clear at the vertex and a forward eastward margin, so the linear
                # segment to the next (also-corrected) vertex stays in clear water.
                if not point_in_geojson(lat, lon, self._exclusion) and not point_in_geojson(
                    lat, lon + 0.02, self._exclusion
                ):
                    break
                lon += 0.004
            out.append((lat, lon))
        return out

    def _centerline_length_nm(self) -> float:
        """Total along-track length of the centreline, in nautical miles. Used to
        convert a vessel's sog into a frac-per-second so implied speed between
        synthetic fixes equals the reported sog (no false POSITION_JUMP)."""
        from .geoutil import haversine_nm as _h
        pts = self._centerline
        total = 0.0
        for a, b in zip(pts, pts[1:]):
            total += _h(a[0], a[1], b[0], b[1])
        return max(total, 1e-6)

    def _point_at(self, frac: float) -> tuple[float, float, float]:
        """Interpolate along the centreline. frac in [0,1] (0=Port Said north,
        1=Suez south). Returns (lat, lon, cog_degrees)."""
        frac = max(0.0, min(1.0, frac))
        pts = self._centerline
        if len(pts) < 2:
            return (pts[0][0], pts[0][1], 180.0)
        seg = frac * (len(pts) - 1)
        i = min(int(seg), len(pts) - 2)
        t = seg - i
        lat = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t
        lon = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t
        cog = self._bearing(pts[i], pts[i + 1])
        return (lat, lon, cog)

    @staticmethod
    def _bearing(a, b) -> float:
        rlat1, rlat2 = math.radians(a[0]), math.radians(b[0])
        dlon = math.radians(b[1] - a[1])
        x = math.sin(dlon) * math.cos(rlat2)
        y = math.cos(rlat1) * math.sin(rlat2) - math.sin(rlat1) * math.cos(rlat2) * math.cos(dlon)
        return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0

    # -- vessel state ------------------------------------------------------
    def _spawn(self) -> dict[int, dict]:
        v: dict[int, dict] = {}
        ship_types = [70, 71, 79, 80, 82, 89, 60, 30]

        # Background transit fleet.
        for k in range(self._n):
            mmsi = 211000000 + k * 13 + self._rng.randint(0, 9)
            northbound = self._rng.random() < 0.5
            v[mmsi] = {
                # Spawn mid-track (0.1-0.9) so few vessels reach an end and
                # reflect during a short demo (a reflection reads as a U-turn).
                "frac": self._rng.uniform(0.1, 0.9),
                "dir": -1 if northbound else 1,   # +1 = south (frac increasing)
                "sog": self._rng.uniform(8.0, 14.0),
                "ship_type": self._rng.choice(ship_types),
                "name": f"TRANSIT {k:02d}",
                "imo": 9000000 + k,
                "role": "background",
                "static_sent": False,
            }

        # PERSIA STAR — the dark vessel. Starts mid-canal in the core, southbound,
        # with enough runway south that the dark-period jump lands ~20 nm away.
        v[DARK_MMSI] = {
            "frac": 0.45, "dir": 1, "sog": 11.0, "ship_type": 82,
            "name": "PERSIA STAR", "imo": 9511223, "role": "dark",
            "static_sent": False, "reappeared": False,
        }
        # GULF LOITERER — STS partner, sits near PERSIA STAR and loiters.
        v[LOITER_MMSI] = {
            "frac": 0.47, "dir": 1, "sog": 10.0, "ship_type": 81,
            "name": "GULF LOITERER", "imo": 9511224, "role": "loiter",
            "static_sent": False, "anchor": None,
        }
        # U-turn convoy — three northbound vessels that reverse mid-canal.
        for idx, mmsi in enumerate(UTURN_MMSIS):
            v[mmsi] = {
                "frac": 0.35 + idx * 0.02, "dir": -1, "sog": 10.0, "ship_type": 70,
                "name": f"CONVOY {idx}", "imo": 9600000 + idx, "role": "uturn",
                "static_sent": False, "reversed": False,
            }
        return v

    # -- envelope builders (AISStream shape) -------------------------------
    @staticmethod
    def _time_utc(epoch: float) -> str:
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S.000000000 +0000 UTC")

    def _position_report(self, mmsi, lat, lon, sog, cog, epoch) -> dict:
        return {
            "MessageType": "PositionReport",
            "MetaData": {
                "MMSI": mmsi, "latitude": lat, "longitude": lon,
                "time_utc": self._time_utc(epoch),
            },
            "Message": {
                "PositionReport": {
                    "UserID": mmsi, "Latitude": lat, "Longitude": lon,
                    "Sog": round(sog, 1), "Cog": round(cog, 1),
                    "TrueHeading": round(cog) % 360, "NavigationalStatus": 0,
                }
            },
        }

    def _ship_static(self, mmsi, lat, lon, vs, epoch) -> dict:
        return {
            "MessageType": "ShipStaticData",
            "MetaData": {
                "MMSI": mmsi, "latitude": lat, "longitude": lon,
                "time_utc": self._time_utc(epoch),
            },
            "Message": {
                "ShipStaticData": {
                    "UserID": mmsi, "Name": vs["name"], "ImoNumber": vs["imo"],
                    "Type": vs["ship_type"], "Destination": "PORT SUEZ",
                    "MaximumStaticDraught": 12.5,
                    "Dimension": {"A": 180, "B": 70, "C": 18, "D": 14},
                }
            },
        }

    # -- the generator -----------------------------------------------------
    async def stream(self) -> AsyncIterator[dict]:
        scenario_t = 0.0
        start_wall = time.time()
        while not self._closed:
            epoch = start_wall + scenario_t
            for mmsi, vs in self._vessels.items():
                for msg in self._step_vessel(mmsi, vs, scenario_t, epoch):
                    yield msg
            # advance scenario time by one report interval
            scenario_t += self._report_interval_s
            await asyncio.sleep(self._tick_s)

    def _step_vessel(self, mmsi, vs, t, epoch):
        """Yield the envelopes this vessel emits this tick, applying its script."""
        out = []
        role = vs["role"]

        # --- DARK vessel: silent during the blackout window ---------------
        if role == "dark" and T_DARK_START <= t < T_DARK_END:
            return out   # emits nothing -> goes dark in the core
        if role == "dark" and t >= T_DARK_END and not vs.get("reappeared"):
            # Reappear ~20 nm south of the blackout point — the "transited dark"
            # jump. The centreline is ~83 nm long, so +0.24 frac ≈ 20 nm.
            vs["frac"] = min(0.95, vs["frac"] + 0.24)
            vs["reappeared"] = True

        # --- LOITER vessel: drop anchor near PERSIA STAR ------------------
        if role == "loiter" and t >= T_LOITER_START:
            if vs.get("anchor") is None:
                lat, lon, _ = self._point_at(vs["frac"])
                vs["anchor"] = (lat + 0.002, lon + 0.002)
            # Slow, smooth orbital drift around the anchor — stays well inside the
            # 2nm / 1.5kn loiter envelope, but is monotonic enough per step that
            # positional bearings don't whipsaw into false REROUTEs.
            phase = vs.get("phase", 0.0) + 0.35
            vs["phase"] = phase
            alat, alon = vs["anchor"]
            lat = alat + 0.0008 * math.sin(phase)
            lon = alon + 0.0008 * math.cos(phase)
            sog = 0.6                          # gentle, < 1.5kn loiter threshold
            cog = (math.degrees(phase) + 90.0) % 360.0
            out.append(self._position_report(mmsi, lat, lon, sog, cog, epoch))
            self._maybe_static(out, mmsi, vs, lat, lon, epoch)
            return out

        # --- U-turn convoy: reverse direction once, mid-canal -------------
        if role == "uturn" and t >= T_UTURN and not vs.get("reversed"):
            vs["dir"] *= -1
            vs["reversed"] = True

        # --- normal transit motion ---------------------------------------
        # Advance frac by the TRUE distance covered this step: sog (nm/h) over the
        # elapsed scenario time, divided by the centreline length. This keeps the
        # implied speed between fixes equal to the reported sog at any pacing,
        # so the spoofing detector does not false-fire on background traffic.
        dt_h = self._report_interval_s / 3600.0
        dfrac = (vs["sog"] * dt_h) / self._track_nm
        vs["frac"] += vs["dir"] * dfrac
        # Reflect at the ends. The position is continuous at the endpoint, so no
        # POSITION_JUMP; the cog flips, which is realistic for a vessel turning.
        if vs["frac"] >= 1.0:
            vs["frac"] = 1.0
            vs["dir"] = -1
        elif vs["frac"] <= 0.0:
            vs["frac"] = 0.0
            vs["dir"] = 1

        lat, lon, cog = self._point_at(vs["frac"])
        if vs["dir"] < 0:
            cog = (cog + 180.0) % 360.0
        # gentle speed jitter
        sog = max(0.1, vs["sog"] + self._rng.uniform(-0.5, 0.5))
        out.append(self._position_report(mmsi, lat, lon, sog, cog, epoch))
        self._maybe_static(out, mmsi, vs, lat, lon, epoch)
        return out

    def _maybe_static(self, out, mmsi, vs, lat, lon, epoch):
        """Emit ShipStaticData once per vessel, on its first fix."""
        if not vs.get("static_sent"):
            out.append(self._ship_static(mmsi, lat, lon, vs, epoch))
            vs["static_sent"] = True

    async def close(self) -> None:
        self._closed = True

    def __aiter__(self) -> AsyncIterator[dict]:
        return self.stream()
