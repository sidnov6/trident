"""Analyst — the investigator.

On escalation the Analyst builds the case. It assembles a vessel dossier (static
identity, recent track, prior incidents), derives flag state from the MMSI MID,
reads the local picture (was another ship dark in the same spot at the same time
-> an STS pair), pulls similar past incidents from institutional memory, folds in
fusion enrichment (sanctions / SAR / weather), and classifies the behaviour into
a :class:`Typology`.

Returns an :class:`AnalystOutput` with an explicit ``reasoning_trace``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

from trident_contracts.enums import SignalType, Typology
from trident_contracts.incident import AnalystOutput
from trident_contracts.signal import Signal
from trident_geo import flag_for_mmsi, is_flag_of_convenience

from ..llm import FALLBACK_MODEL, model_name, structured

log = logging.getLogger("cognition.agents.analyst")

PROMPT_VERSION = "analyst-v1"


class _Classification(BaseModel):
    """The narrow slice we ask the LLM to decide; the rest of AnalystOutput
    (fusion fields, sts partner) is assembled deterministically around it."""

    typology: Typology = Typology.BENIGN
    severity: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    summary: str = ""
    reasoning_trace: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """You are the ANALYST, lead investigator of a maritime chokepoint \
intelligence cell. You receive a correlated incident: a vessel, its dossier (flag, \
type, track, prior incidents), the merged detector signals, the local picture \
(nearby/co-dark vessels), and similar past cases.

Classify the behaviour into exactly one typology:
 - SANCTIONS_EVASION: dark vessel + flag-of-convenience / flag-hopping / shadow-fleet \
markers, especially with a sanctions or prior-incident link.
 - STS_TRANSFER: two vessels loitering hull-to-hull, often one or both dark — a \
ship-to-ship transfer.
 - SMUGGLING_COVER: deceptive manoeuvring (reroute, u-turn, position jump, identity \
conflict) consistent with concealment.
 - NAV_HAZARD: loitering/congestion/geofence consistent with navigational risk, not \
intent.
 - MILITARY_ACTIVITY: patterns consistent with naval/grey-zone activity.
 - BENIGN: best explained by ordinary operations or environment.

Produce a calibrated severity and confidence in [0,1], a one-paragraph summary, and \
an explicit step-by-step reasoning_trace (each step a short string). Be rigorous, \
cite the evidence, and do not over-claim beyond what the dossier supports."""


# ---------------------------------------------------------------------------
# Deterministic classifier (no-Groq fallback)
# ---------------------------------------------------------------------------

def _classify_deterministic(
    signal: Signal,
    merged: list[Signal],
    dossier: dict[str, Any],
    sts_partner: Optional[int],
    sanctions_hit: Optional[dict[str, Any]],
    similar: list[dict[str, Any]],
) -> _Classification:
    """Transparent rule-based typology assignment.

    Encodes the same heuristics the prompt describes so the offline graph yields
    a sensible, fully-traced classification labelled deterministic-fallback.
    """
    types = {s.type for s in merged}
    flag = dossier.get("flag")
    foc = is_flag_of_convenience(flag)
    max_sev = max((s.severity for s in merged), default=signal.severity)
    priors = dossier.get("incident_ids") or []

    trace: list[str] = []
    trace.append(
        f"Vessel MMSI {signal.mmsi} ({dossier.get('name') or 'unknown name'}), "
        f"flag {flag or 'unknown'}{' [flag-of-convenience]' if foc else ''}, "
        f"type code {dossier.get('ship_type')}."
    )
    trace.append(
        "Merged signals: " + ", ".join(sorted(t.value for t in types)) +
        f"; peak severity {max_sev:.2f}."
    )
    if priors:
        trace.append(f"{len(priors)} prior incident(s) on record for this MMSI.")
    if similar:
        trace.append(
            "Institutional memory: similar to " +
            ", ".join(f"{c.get('typology')} (score {c.get('score')})" for c in similar[:3])
            + "."
        )
    if sanctions_hit:
        trace.append(
            f"SANCTIONS HIT on {sanctions_hit.get('matched_on')} "
            f"(program {sanctions_hit.get('program')})."
        )

    # Decision cascade, most-specific first.
    has_dark = SignalType.DARK_VESSEL in types
    has_loiter = SignalType.LOITERING in types
    deceptive = types & {
        SignalType.REROUTE, SignalType.UTURN,
        SignalType.POSITION_JUMP, SignalType.IDENTITY_CONFLICT,
    }

    if sts_partner is not None and has_loiter:
        typ = Typology.STS_TRANSFER
        conf = 0.75
        trace.append(
            f"Co-located dark/loitering partner MMSI {sts_partner} at the same "
            f"position and time -> ship-to-ship transfer pattern."
        )
    elif has_dark and (foc or sanctions_hit or priors):
        typ = Typology.SANCTIONS_EVASION
        conf = 0.8 if sanctions_hit else 0.65
        trace.append(
            "Dark-vessel behaviour combined with "
            + ("a sanctions hit" if sanctions_hit else
               "flag-of-convenience" if foc else "prior incidents")
            + " indicates deliberate AIS suppression to evade sanctions."
        )
    elif has_dark:
        typ = Typology.SANCTIONS_EVASION
        conf = 0.5
        trace.append("Dark vessel without corroborating markers — provisional sanctions-evasion read at reduced confidence.")
    elif deceptive:
        typ = Typology.SMUGGLING_COVER
        conf = 0.5
        trace.append(
            "Deceptive manoeuvring (" + ", ".join(sorted(t.value for t in deceptive)) +
            ") consistent with concealment of intent."
        )
    elif has_loiter or types & {SignalType.CONGESTION, SignalType.GEOFENCE_BREACH}:
        typ = Typology.NAV_HAZARD
        conf = 0.45
        trace.append("Loitering/congestion without deceptive markers — navigational hazard, not intent.")
    else:
        typ = Typology.BENIGN
        conf = 0.4
        trace.append("No pattern rises above ordinary operations; assessed benign.")

    # Severity blends detector severity with the typology's intrinsic alarm and
    # any sanctions amplification.
    typ_weight = {
        Typology.SANCTIONS_EVASION: 0.9,
        Typology.STS_TRANSFER: 0.85,
        Typology.SMUGGLING_COVER: 0.7,
        Typology.MILITARY_ACTIVITY: 0.95,
        Typology.NAV_HAZARD: 0.5,
        Typology.BENIGN: 0.15,
    }[typ]
    severity = round(min(1.0, 0.5 * max_sev + 0.5 * typ_weight + (0.1 if sanctions_hit else 0.0)), 3)

    summary = (
        f"{typ.value.replace('_', ' ').title()} assessed for MMSI {signal.mmsi} in "
        f"{signal.zone}: {', '.join(sorted(t.value for t in types))}"
        + (f", co-dark partner {sts_partner}" if sts_partner else "")
        + (f", sanctions program {sanctions_hit.get('program')}" if sanctions_hit else "")
        + f". Severity {severity:.2f}, confidence {conf:.2f}."
    )
    trace.append(f"Verdict: {typ.value} at severity {severity:.2f}, confidence {conf:.2f}.")

    return _Classification(
        typology=typ,
        severity=severity,
        confidence=conf,
        summary=summary,
        reasoning_trace=trace,
    )


async def run_analyst(
    signal: Signal,
    merged: list[Signal],
    dossier: dict[str, Any],
    *,
    sts_partner: Optional[int] = None,
    fusion: Optional[dict[str, Optional[dict[str, Any]]]] = None,
    similar: Optional[list[dict[str, Any]]] = None,
) -> AnalystOutput:
    """Investigate and classify. Returns a fully-populated AnalystOutput.

    The fusion fields are assigned from ``fusion`` (the result map from
    ``cognition.fusion.run_fusion``); they remain None when fusion is disabled.
    """
    fusion = fusion or {}
    similar = similar or []
    sanctions_hit = fusion.get("sanctions_match")

    runnable = structured(_Classification)
    if runnable is None:
        cls = _classify_deterministic(
            signal, merged, dossier, sts_partner, sanctions_hit, similar
        )
        log.info("[analyst/%s] %s sev=%.2f", FALLBACK_MODEL, cls.typology.value, cls.severity)
    else:
        dossier_brief = {
            "name": dossier.get("name"),
            "flag": dossier.get("flag"),
            "ship_type": dossier.get("ship_type"),
            "imo": dossier.get("imo"),
            "track_points": len(dossier.get("track") or []),
            "prior_incidents": len(dossier.get("incident_ids") or []),
        }
        user = (
            f"INCIDENT on MMSI {signal.mmsi} in zone {signal.zone}.\n"
            f"DOSSIER: {dossier_brief}\n"
            f"MERGED SIGNALS: "
            + "; ".join(f"{s.type.value}(sev={s.severity:.2f})" for s in merged)
            + f"\nLOCAL PICTURE: sts_partner_mmsi={sts_partner}\n"
            f"FUSION: sanctions_match={sanctions_hit}, "
            f"sar_confirmation={fusion.get('sar_confirmation')}, "
            f"weather_context={fusion.get('weather_context')}\n"
            f"SIMILAR PAST CASES: "
            + (", ".join(f"{c.get('typology')}({c.get('score')})" for c in similar) or "none")
            + "\nClassify with a calibrated severity, confidence, summary and reasoning_trace."
        )
        try:
            cls = await runnable.ainvoke([("system", SYSTEM_PROMPT), ("human", user)])
            log.info("[analyst/%s] %s sev=%.2f", model_name(), cls.typology.value, cls.severity)
        except Exception as exc:  # pragma: no cover
            log.warning("[analyst] LLM call failed (%s); using deterministic fallback.", exc)
            cls = _classify_deterministic(
                signal, merged, dossier, sts_partner, sanctions_hit, similar
            )

    return AnalystOutput(
        typology=cls.typology,
        severity=cls.severity,
        confidence=cls.confidence,
        summary=cls.summary,
        reasoning_trace=cls.reasoning_trace,
        sts_partner_mmsi=sts_partner,
        sanctions_match=sanctions_hit,
        sar_confirmation=fusion.get("sar_confirmation"),
        weather_context=fusion.get("weather_context"),
        osint_context=fusion.get("osint_context"),
    )


# ---------------------------------------------------------------------------
# Dossier + local-picture assembly helpers (used by the graph node)
# ---------------------------------------------------------------------------

async def assemble_dossier(pool: Any | None, mmsi: int) -> dict[str, Any]:
    """Build a vessel dossier from Postgres: static identity, recent track, prior
    incidents. Flag is always derived from the MMSI MID (zero external calls);
    DB static flag, if present, takes precedence."""
    dossier: dict[str, Any] = {
        "mmsi": mmsi,
        "flag": flag_for_mmsi(mmsi),
        "track": [],
        "incident_ids": [],
    }
    if pool is None:
        return dossier
    try:
        async with pool.acquire() as conn:
            vrow = await conn.fetchrow(
                "SELECT imo, name, ship_type, flag, destination, draught, length, beam "
                "FROM vessels WHERE mmsi = $1",
                mmsi,
            )
            if vrow:
                v = dict(vrow)
                dossier.update({k: v[k] for k in v if v[k] is not None})
                dossier.setdefault("flag", flag_for_mmsi(mmsi))
            track = await conn.fetch(
                "SELECT extract(epoch FROM ts) AS ts, ST_Y(geom) AS lat, ST_X(geom) AS lon "
                "FROM tracks WHERE mmsi = $1 ORDER BY ts DESC LIMIT 200",
                mmsi,
            )
            dossier["track"] = [(r["ts"], r["lat"], r["lon"]) for r in track]
            priors = await conn.fetch(
                "SELECT id::text FROM incidents WHERE mmsi = $1 ORDER BY opened_at DESC LIMIT 25",
                mmsi,
            )
            dossier["incident_ids"] = [r["id"] for r in priors]
    except Exception as exc:  # pragma: no cover - DB optional
        log.debug("dossier assembly partial (%s)", exc)
    return dossier


async def detect_sts_partner(
    pool: Any | None,
    signal: Signal,
    *,
    radius_m: float = 1852.0,
    window_s: float = 1800.0,
) -> Optional[int]:
    """Local picture: was another vessel at the same spot at the same time?

    Returns the MMSI of the nearest *other* hull within ``radius_m`` of the
    signal position around ``signal.ts`` — the candidate STS partner. Uses
    PostGIS ``ST_DWithin`` on the track hypertable.
    """
    if pool is None:
        return None
    lat, lon = signal.position
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT mmsi,
                       ST_Distance(geom::geography,
                                   ST_SetSRID(ST_MakePoint($2, $1), 4326)::geography) AS dist
                FROM tracks
                WHERE mmsi <> $3
                  AND ts BETWEEN to_timestamp($4 - $6) AND to_timestamp($4 + $6)
                  AND ST_DWithin(geom::geography,
                                 ST_SetSRID(ST_MakePoint($2, $1), 4326)::geography, $5)
                ORDER BY dist ASC
                LIMIT 1
                """,
                lat, lon, signal.mmsi, signal.ts, radius_m, window_s,
            )
    except Exception as exc:  # pragma: no cover - DB optional
        log.debug("STS proximity query skipped (%s)", exc)
        return None
    return int(row["mmsi"]) if row else None
