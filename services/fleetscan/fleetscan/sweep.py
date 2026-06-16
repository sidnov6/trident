"""The deterministic fleet sweep: scan world-state, classify, publish alerts.

Runs every SCAN_INTERVAL_S over the GLOBAL_GEO index. Pure-Python per-vessel
rules + a couple of cheap cross-vessel passes (STS proximity, sanctions
co-occurrence). No DB. Cross-sweep state is in-process and pruned.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from trident_common import keys
from trident_contracts.enums import ShipTypeBucket, ThreatCategory
from trident_contracts.fleet_alert import FleetAlert

from . import config as C
from .agents import PER_VESSEL_AGENTS
from .agents.base import AgentHit
from .agents.classifiers import _is_tanker
from .memory import AgentMemory, Snapshot, parse_hash

log = logging.getLogger("fleetscan.sweep")
DETECTOR_VERSION = "fleet-1.0.0"


class FleetSweep:
    def __init__(self, redis: Any) -> None:
        self._redis = redis
        self._mem: dict[int, AgentMemory] = {}

    async def run_once(self, now: float) -> int:
        """One full sweep. Returns the number of alerts published."""
        try:
            members = await self._redis.zrange(keys.GLOBAL_GEO, 0, -1)
        except Exception:
            log.warning("GLOBAL_GEO read failed", exc_info=True)
            return 0
        mmsis: list[int] = []
        for m in members or ():
            if isinstance(m, bytes):
                m = m.decode()
            try:
                mmsis.append(int(m))
            except (TypeError, ValueError):
                continue

        snaps: dict[int, Snapshot] = {}
        hits: dict[int, list[AgentHit]] = {}
        seen: set[int] = set()

        # --- pass 1: per-vessel classification (pipelined HGETALL) ---------
        for i in range(0, len(mmsis), C.SWEEP_BATCH):
            chunk = mmsis[i:i + C.SWEEP_BATCH]
            try:
                pipe = self._redis.pipeline(transaction=False)
                for mmsi in chunk:
                    pipe.hgetall(keys.vessel_key(mmsi))
                raws = await pipe.execute()
            except Exception:
                continue
            for mmsi, raw in zip(chunk, raws):
                snap = parse_hash(mmsi, raw)
                if snap is None:
                    continue
                snaps[mmsi] = snap
                seen.add(mmsi)
                mem = self._mem.setdefault(mmsi, AgentMemory())
                vhits = []
                for agent in PER_VESSEL_AGENTS:
                    try:
                        hit = agent.classify(snap, mem, now)
                    except Exception:
                        continue
                    if hit:
                        vhits.append(hit)
                if vhits:
                    hits[mmsi] = vhits
                # remember for next sweep + prune marker
                mem.prev_lat, mem.prev_lon, mem.prev_ts = snap.lat, snap.lon, snap.last_fix_ts
                mem.last_seen_sweep = now

        # --- pass 2: STS rendezvous (proximity, only for loitering tankers)
        await self._sts_pass(snaps, hits, now)

        # --- pass 3: sanctions co-occurrence (DARK_FLEET + GONE_DARK) ------
        for mmsi, vhits in list(hits.items()):
            cats = {h.category for h in vhits}
            if ThreatCategory.DARK_FLEET.value in cats and ThreatCategory.GONE_DARK.value in cats:
                vhits.append(AgentHit(
                    ThreatCategory.SANCTIONS_RISK.value, 0.85, 0.55,
                    ["Shadow-tanker profile AND went dark — sanctions-evasion signature"],
                ))

        # --- publish (dedupe + composite risk + breadcrumbs) --------------
        published = 0
        for mmsi, vhits in hits.items():
            snap = snaps[mmsi]
            risk = _composite_risk(vhits, snap)
            for hit in vhits:
                if await self._should_publish(snap, hit):
                    await self._publish(snap, hit, risk, now)
                    published += 1
            if risk >= C.RISK_FLAG_THRESHOLD:
                await self._flag(snap, vhits, risk, now)

        # --- prune stale memory -------------------------------------------
        stale = [m for m, mem in self._mem.items()
                 if m not in seen and (now - mem.last_seen_sweep) > C.MEMORY_TTL_S]
        for m in stale:
            self._mem.pop(m, None)

        return published

    async def _sts_pass(self, snaps, hits, now) -> None:
        for mmsi, snap in snaps.items():
            cats = {h.category for h in hits.get(mmsi, ())}
            if ThreatCategory.LOITERING.value not in cats or not _is_tanker(snap):
                continue
            try:
                near = await self._redis.geosearch(
                    keys.GLOBAL_GEO,
                    longitude=snap.lon, latitude=snap.lat,
                    radius=C.STS_RADIUS_NM * 1852.0, unit="m",
                )
            except Exception:
                continue
            for nm in near or ():
                if isinstance(nm, bytes):
                    nm = nm.decode()
                try:
                    other = int(nm)
                except (TypeError, ValueError):
                    continue
                if other == mmsi:
                    continue
                o = snaps.get(other)
                if o and o.sog < 1.5 and _is_tanker(o):
                    sev = 0.7 + (0.2 if (snap.is_foc or o.is_foc) else 0.0)
                    hits.setdefault(mmsi, []).append(AgentHit(
                        ThreatCategory.STS_TRANSFER.value, sev, 0.7,
                        [f"Rafted with another slow tanker (MMSI {other}) at sea"],
                    ))
                    break

    async def _should_publish(self, snap: Snapshot, hit: AgentHit) -> bool:
        mem = self._mem.get(snap.mmsi)
        last = (mem.last_published.get(hit.category) if mem else None)
        # Escalation bypasses cooldown.
        if last is not None and hit.severity - last >= C.ESCALATION_DELTA:
            if mem:
                mem.last_published[hit.category] = hit.severity
            return True
        try:
            ok = await self._redis.set(
                keys.fleet_cooldown_key(snap.mmsi, hit.category),
                "1", nx=True, ex=C.COOLDOWN_S,
            )
        except Exception:
            ok = True
        if ok and mem:
            mem.last_published[hit.category] = hit.severity
        return bool(ok)

    async def _publish(self, snap: Snapshot, hit: AgentHit, risk: float, now: float) -> None:
        alert = FleetAlert(
            ts=now, category=ThreatCategory(hit.category), agent=hit.category,
            mmsi=snap.mmsi, name=snap.name, flag=snap.flag, ship_bucket=snap.bucket,
            severity=hit.severity, confidence=hit.confidence, risk=risk,
            position=(snap.lat, snap.lon), cog=snap.cog, sog=snap.sog, zone=snap.zone,
            evidence=hit.evidence, detector_version=DETECTOR_VERSION,
        )
        try:
            await self._redis.xadd(
                keys.STREAM_FLEET_ALERTS, alert.to_stream_fields(),
                maxlen=5000, approximate=True,
            )
        except Exception:
            log.debug("xadd fleet alert failed", exc_info=True)

    async def _flag(self, snap: Snapshot, vhits, risk: float, now: float) -> None:
        """High-risk vessel -> watchlist + a breadcrumb for the path/origin view."""
        import json

        top = max(vhits, key=lambda h: h.severity)
        try:
            await self._redis.sadd(keys.WATCHLIST_PRIORITY, snap.mmsi)
            await self._redis.hset(keys.WATCHLIST_META, str(snap.mmsi), json.dumps({
                "category": top.category, "reason": top.evidence[0] if top.evidence else "",
                "flagged_ts": now, "risk": round(risk, 3),
            }))
            tk = keys.fleet_track_key(snap.mmsi)
            await self._redis.rpush(tk, f"{snap.last_fix_ts or now},{snap.lat},{snap.lon}")
            await self._redis.ltrim(tk, -C.BREADCRUMB_MAX, -1)
            await self._redis.expire(tk, keys.VESSEL_TTL_S)
        except Exception:
            log.debug("flag write failed", exc_info=True)


def _composite_risk(vhits, snap: Snapshot) -> float:
    if not vhits:
        return 0.0
    scored = []
    for h in vhits:
        w = C.CATEGORY_WEIGHT.get(h.category, 0.4)
        scored.append(w * h.severity * h.confidence)
    base = max(scored)
    boost = C.CORROBORATION_BOOST * sum(1 for sv in scored if sv > 0.3) - C.CORROBORATION_BOOST
    boost = max(0.0, boost)
    mult = 1.0 + (C.FOC_MULTIPLIER if snap.is_foc else 0.0)
    return max(0.0, min(1.0, (base + boost) * mult))
