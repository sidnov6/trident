"""The deterministic fleet agents (one per danger category).

STS_TRANSFER + SANCTIONS_RISK are derived in sweep.py (they need a proximity
query / cross-category co-occurrence); the rest are pure per-vessel rules here.
"""
from __future__ import annotations

from trident_contracts.enums import ShipTypeBucket, ThreatCategory

from .. import config as C
from ..geoutil import haversine_nm, implied_speed_kn
from ..memory import AgentMemory, Snapshot
from .base import AgentHit, FleetAgent


def _is_tanker(s: Snapshot) -> bool:
    if s.bucket == int(ShipTypeBucket.TANKER):
        return True
    # ONLY when the type is genuinely unknown (AIS type 0 -> bucket OTHER): a
    # laden vessel with an IMO is likely a tanker. Never override a KNOWN
    # non-tanker type (cargo/passenger/etc.).
    if s.bucket == int(ShipTypeBucket.OTHER):
        return bool(s.imo) and (s.draught or 0) >= C.DRAUGHT_TANKER_M
    return False


def _dest_missing(s: Snapshot) -> bool:
    d = (s.destination or "").strip().upper()
    return d in ("", "---", "UNKNOWN", "N/A", "NA", "0")


class GoneDarkAgent(FleetAgent):
    category = ThreatCategory.GONE_DARK.value
    name = "Went-Dark Agent"

    def classify(self, s: Snapshot, mem: AgentMemory, now: float) -> AgentHit | None:
        gap = now - s.last_fix_ts if s.last_fix_ts else 0.0
        if gap > C.GONE_DARK_GAP_S and s.sog > C.MOVING_SOG:
            if not mem.was_dark:
                mem.was_dark = True
                mem.dark_lat, mem.dark_lon = s.lat, s.lon
            mins = gap / 60.0
            sev = min(1.0, 0.6 + mins / 240.0)
            return AgentHit(self.category, sev, 0.8,
                            [f"No AIS signal for {mins:.0f} min while underway"])
        # fresh fix — did it reappear after going dark?
        if mem.was_dark and gap < C.GONE_DARK_GAP_S:
            disp = haversine_nm(mem.dark_lat or s.lat, mem.dark_lon or s.lon, s.lat, s.lon)
            mem.was_dark = False
            if disp > 1.0:
                sev = min(1.0, 0.7 + disp / 100.0)
                return AgentHit(self.category, sev, 0.85,
                                [f"Reappeared {disp:.0f} nm away after going dark"])
        return None


class DarkFleetAgent(FleetAgent):
    category = ThreatCategory.DARK_FLEET.value
    name = "Shadow-Tanker Agent"

    def classify(self, s: Snapshot, mem: AgentMemory, now: float) -> AgentHit | None:
        # FOC tankers are extremely common and mostly legitimate, so the bare
        # profile (tanker + flag-of-convenience) is NOT enough to alert — that
        # would flag every Panama/Liberia/Malta tanker. Require an actual
        # behavioural red flag on top of the profile.
        if not (_is_tanker(s) and s.is_foc):
            return None
        dark = mem.was_dark
        ev = [f"Tanker under a flag of convenience ({s.flag})"]
        if dark:
            ev.append("Has gone dark recently")
        elif _dest_missing(s):
            ev.append("Hiding its destination")
        elif s.nav_status == C.NAV_UNDEFINED and s.sog > 3.0:
            ev.append("Moving with an undefined navigation status")
        else:
            return None  # plain FOC tanker behaving normally -> not an alert
        sev = 0.6 + (0.4 if dark else 0.0)
        return AgentHit(self.category, sev, 0.7, ev)


class SpoofingAgent(FleetAgent):
    category = ThreatCategory.SPOOFING.value
    name = "Position-Spoof Agent"

    def classify(self, s: Snapshot, mem: AgentMemory, now: float) -> AgentHit | None:
        if mem.prev_ts and mem.prev_lat is not None and s.last_fix_ts:
            implied = implied_speed_kn(mem.prev_lat, mem.prev_lon, mem.prev_ts,
                                       s.lat, s.lon, s.last_fix_ts)
            if implied > C.TELEPORT_KN:
                sev = min(1.0, 0.6 + implied / 200.0)
                return AgentHit(self.category, sev, 0.8,
                                [f"Jumped position at an impossible {implied:.0f} kn"])
        return None


class LoiteringAgent(FleetAgent):
    category = ThreatCategory.LOITERING.value
    name = "Loitering Agent"

    def classify(self, s: Snapshot, mem: AgentMemory, now: float) -> AgentHit | None:
        underway_status = s.nav_status not in (C.NAV_AT_ANCHOR, C.NAV_MOORED)
        if s.sog < C.LOITER_SOG and underway_status and s.bucket != int(ShipTypeBucket.FISHING):
            mem.loiter_streak += 1
            # The world is full of legitimately slow/stopped ships, so plain
            # loitering is not alarming. Only flag loitering that is actually
            # suspicious: a TANKER or a flag-of-convenience vessel (pre-STS /
            # waiting-for-orders signature). Everything else just resets streak.
            if mem.loiter_streak >= C.LOITER_SWEEPS and (_is_tanker(s) or s.is_foc):
                mins = mem.loiter_streak * C.SCAN_INTERVAL_S / 60.0
                return AgentHit(self.category, 0.6, 0.7,
                                [f"{'Tanker' if _is_tanker(s) else 'Vessel'} sitting "
                                 f"nearly still in open water (~{mins:.0f} min)"])
        else:
            mem.loiter_streak = 0
        return None


class NavHazardAgent(FleetAgent):
    category = ThreatCategory.NAV_HAZARD.value
    name = "Aground/Blockage Agent"

    def classify(self, s: Snapshot, mem: AgentMemory, now: float) -> AgentHit | None:
        if s.nav_status == C.NAV_AGROUND:
            return AgentHit(self.category, 0.85, 0.9, ["Reporting AGROUND — blockage risk"])
        return None


class GreyZoneAgent(FleetAgent):
    category = ThreatCategory.GREY_ZONE.value
    name = "Grey-Zone Agent"

    def classify(self, s: Snapshot, mem: AgentMemory, now: float) -> AgentHit | None:
        nm = (s.name or "").upper()
        if any(tok in nm for tok in C.GREY_ZONE_NAME_TOKENS):
            contested = s.zone in ("hormuz", "bab_el_mandeb")
            sev = 0.8 if contested else 0.6
            ev = ["Name suggests a naval / patrol vessel"]
            if contested:
                ev.append("Operating in a contested chokepoint")
            return AgentHit(self.category, sev, 0.6 if contested else 0.45, ev)
        return None


# Order matters a little: GoneDark runs before DarkFleet so mem.was_dark is set.
PER_VESSEL_AGENTS = [
    GoneDarkAgent(),
    DarkFleetAgent(),
    SpoofingAgent(),
    LoiteringAgent(),
    NavHazardAgent(),
    GreyZoneAgent(),
]
