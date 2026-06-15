"""Sentinel — the correlator.

First contact. Holds a short per-zone episodic buffer of recent signals and
decides, for each new firing, whether it is *noise* (drop), a *new* incident, or
another facet of an incident already in flight on the same MMSI — in which case
it merges rather than spawns. The classic case: a DARK_VESSEL, a LOITERING and a
REROUTE on one hull within minutes are ONE story, not three alarms.

Returns a :class:`SentinelOutput`.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from trident_contracts.signal import Signal
from trident_contracts.incident import SentinelOutput

from ..llm import FALLBACK_MODEL, model_name, structured

log = logging.getLogger("cognition.agents.sentinel")

PROMPT_VERSION = "sentinel-v1"

# Signal types that, on their own, are rarely worth waking the Analyst. They
# escalate only in combination or at high severity.
_LOW_VALUE_ALONE = {"CONGESTION"}

# Below this severity a lone signal is treated as ambient noise.
_NOISE_SEVERITY = 0.25

SYSTEM_PROMPT = """You are SENTINEL, the correlation watch officer of a maritime \
chokepoint intelligence cell. You receive discrete detector signals (dark vessel, \
loitering, reroute, position jump, identity conflict, congestion, geofence breach, \
u-turn) and a short buffer of recent signals in the same zone.

Your job is triage and coalescence, NOT investigation:
 - Decide whether the NEW signal is noise to drop, or evidence worth escalating.
 - Merge signals that belong to the SAME unfolding event on the SAME vessel within \
a few minutes into ONE incident — never raise three alarms for one hull going \
dark, loitering and rerouting together.
 - A lone low-severity congestion blip is usually noise. A dark vessel, an identity \
conflict, a reroute paired with loitering, or anything high-severity escalates.

Return the merged signal id list, an escalate decision, and a terse naval-watch \
rationale. Be decisive and economical."""


def _merge_candidates(signal: Signal, recent: list[Signal]) -> list[Signal]:
    """Recent same-MMSI signals (including this one) form the merge set."""
    same = [s for s in recent if s.mmsi == signal.mmsi]
    ids = {s.id for s in same}
    if signal.id not in ids:
        same = [*same, signal]
    return same


def _deterministic(signal: Signal, recent: list[Signal]) -> SentinelOutput:
    """Rule-based correlator used when no Groq client is available.

    Mirrors the LLM's mandate with explicit, auditable rules so the graph still
    produces sensible incidents fully offline.
    """
    merged = _merge_candidates(signal, recent)
    merged_types = {s.type.value for s in merged}
    max_sev = max((s.severity for s in merged), default=signal.severity)

    # Escalate when: any signal is meaningfully severe, OR multiple correlated
    # signal types stack on one hull, OR the signal is intrinsically high-value
    # (anything that isn't lone low-value congestion).
    escalate = (
        max_sev >= _NOISE_SEVERITY
        and (
            len(merged_types) >= 2
            or not merged_types.issubset(_LOW_VALUE_ALONE)
        )
    )

    if escalate and len(merged) >= 2:
        rationale = (
            f"Coalesced {len(merged)} signals on MMSI {signal.mmsi} in {signal.zone} "
            f"({', '.join(sorted(merged_types))}) into one incident; peak severity "
            f"{max_sev:.2f}. Escalating to Analyst."
        )
    elif escalate:
        rationale = (
            f"{signal.type.value} on MMSI {signal.mmsi} in {signal.zone} at severity "
            f"{signal.severity:.2f} clears the noise floor. Escalating."
        )
    else:
        rationale = (
            f"Lone low-value {signal.type.value} (severity {signal.severity:.2f}) "
            f"on MMSI {signal.mmsi}; below escalation floor. Dropping as ambient noise."
        )

    return SentinelOutput(
        mmsi=signal.mmsi,
        zone=signal.zone,
        merged_signals=[s.id for s in merged],
        escalate=escalate,
        rationale=rationale,
    )


async def run_sentinel(signal: Signal, recent: list[Signal]) -> SentinelOutput:
    """Correlate ``signal`` against the recent per-zone window.

    Uses Groq with structured output when available; otherwise the deterministic
    rule-based correlator. Either way returns a typed SentinelOutput.
    """
    runnable = structured(SentinelOutput)
    if runnable is None:
        out = _deterministic(signal, recent)
        log.info("[sentinel/%s] escalate=%s %s", FALLBACK_MODEL, out.escalate, out.rationale)
        return out

    merged = _merge_candidates(signal, recent)
    context_lines = "\n".join(
        f"- {s.type.value} mmsi={s.mmsi} sev={s.severity:.2f} ts={s.ts:.0f}"
        for s in merged
    )
    user = (
        f"NEW SIGNAL: {signal.type.value} on MMSI {signal.mmsi} in zone "
        f"{signal.zone}, severity {signal.severity:.2f}, confidence "
        f"{signal.confidence:.2f}, position {signal.position}, evidence "
        f"{signal.evidence}.\n\nRECENT SAME-VESSEL SIGNALS (merge horizon):\n"
        f"{context_lines or '(none)'}\n\nDecide: merge set, escalate, rationale."
    )
    try:
        out: SentinelOutput = await runnable.ainvoke(
            [("system", SYSTEM_PROMPT), ("human", user)]
        )
    except Exception as exc:  # pragma: no cover - network/LLM guard
        log.warning("[sentinel] LLM call failed (%s); using deterministic fallback.", exc)
        return _deterministic(signal, recent)

    # The model owns the verdict but we anchor the identity fields and ensure the
    # triggering signal is always in the merge set. The incident_id is ALWAYS
    # system-assigned — the LLM must never invent it (the incidents.id column is a
    # UUID; a human-readable id like "DARK_VESSEL_636092123" would fail the insert).
    out.incident_id = str(uuid.uuid4())
    out.mmsi = signal.mmsi
    out.zone = signal.zone
    if signal.id not in out.merged_signals:
        out.merged_signals.append(signal.id)
    log.info("[sentinel/%s] escalate=%s %s", model_name(), out.escalate, out.rationale)
    return out
