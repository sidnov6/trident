"""The cognition state machine (LangGraph).

    Signal in
        -> sentinel   (correlate + dedupe)
        -> [drop if noise]            (conditional edge)
        -> analyst    (enrich + classify)
        -> desk       (economic impact)
        -> gate       (severity >= tau ?)
        -> [human_review | persist]   (conditional edge)

The graph is durable: it uses a Postgres checkpointer keyed by a thread_id, so an
incident under investigation survives a process restart. Every agent node also
accrues an append-only audit entry; the persist node flushes them with the
incident.

The whole graph runs end-to-end with NO Groq key — every agent has a deterministic
fallback (see ``cognition.agents.*``). When the key is absent, audit rows are
stamped model="deterministic-fallback".
"""
from __future__ import annotations

import logging
import time
from typing import Annotated, Any, Optional

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from trident_common.settings import get_settings
from trident_contracts.enums import IncidentStatus
from trident_contracts.incident import (
    AnalystOutput,
    AuditEntry,
    DeskOutput,
    Incident,
    SentinelOutput,
)
from trident_contracts.signal import Signal

from . import persistence
from .agents import analyst as analyst_mod
from .agents import desk as desk_mod
from .agents import sentinel as sentinel_mod
from .agents.analyst import assemble_dossier, detect_sts_partner, run_analyst
from .agents.desk import run_desk
from .agents.sentinel import run_sentinel
from .fusion import build_adapters, run_fusion
from .llm import model_name
from .memory import EpisodicBuffer

log = logging.getLogger("cognition.graph")


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class GraphState(TypedDict, total=False):
    """The state threaded through the machine and checkpointed in Postgres.

    Note the buffer is carried as a plain list of signal dicts (not a live
    EpisodicBuffer object) so it serialises cleanly into the checkpoint.
    """

    signal: dict[str, Any]                 # incoming Signal (json mode)
    episodic: list[dict[str, Any]]         # per-zone recent-signal window
    merged_signals: list[dict[str, Any]]   # signals the Sentinel coalesced
    sentinel: Optional[dict[str, Any]]     # SentinelOutput
    analyst: Optional[dict[str, Any]]      # AnalystOutput
    desk: Optional[dict[str, Any]]         # DeskOutput
    incident: Optional[dict[str, Any]]     # Incident
    audit: list[dict[str, Any]]            # accumulated AuditEntry rows
    dropped: bool                          # sentinel said noise
    needs_review: bool                     # gate routed to human_review


class Deps:
    """Runtime handles injected into the graph (datastores + fusion adapters).

    LangGraph nodes are plain callables; we close over a single Deps instance so
    the nodes stay pure-ish and testable. ``pool`` / ``redis`` may be None offline.
    """

    def __init__(self, pool: Any | None = None, redis: Any | None = None) -> None:
        self.pool = pool
        self.redis = redis
        self.adapters = build_adapters(pool=pool)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def _node_sentinel(state: GraphState, deps: Deps) -> dict[str, Any]:
    signal = Signal.model_validate(state["signal"])
    buf = EpisodicBuffer.from_list(state.get("episodic"))
    recent = buf.recent_for_mmsi(signal.mmsi)
    buf.add(signal)  # remember this firing for the next signal in the zone

    out: SentinelOutput = await run_sentinel(signal, recent)

    # The merge set: the recent signals named in merged_signals, plus this one.
    by_id = {s.id: s for s in [*recent, signal]}
    merged = [by_id[i] for i in out.merged_signals if i in by_id] or [signal]

    audit = build_audit_block(
        out.incident_id, "sentinel",
        node_input={"signal": state["signal"], "recent_ids": [s.id for s in recent]},
        output=out.model_dump(mode="json"),
        prompt_version=sentinel_mod.PROMPT_VERSION,
    )

    # Sentinel is the entry node. The graph runs on a DURABLE per-zone thread so the
    # episodic window survives restarts — but that means every other field is still
    # in the checkpoint from the PREVIOUS incident on this thread. Reset all
    # per-incident state here (keeping only the carried-over episodic buffer), so
    # the audit chain and incident are scoped to THIS signal and never leak across
    # incidents (which previously replayed a stale incident_id into the audit write).
    return {
        "episodic": buf.to_list(),
        "sentinel": out.model_dump(mode="json"),
        "merged_signals": [s.model_dump(mode="json") for s in merged],
        "dropped": not out.escalate,
        "audit": [audit],          # RESET — not [*state["audit"], audit]
        "analyst": None,
        "desk": None,
        "incident": None,
        "needs_review": False,
    }


async def _node_analyst(state: GraphState, deps: Deps) -> dict[str, Any]:
    signal = Signal.model_validate(state["signal"])
    sentinel = SentinelOutput.model_validate(state["sentinel"])
    merged = [Signal.model_validate(s) for s in state.get("merged_signals", [])] or [signal]

    # Enrich: dossier, local picture (STS partner), fusion, institutional memory.
    dossier = await assemble_dossier(deps.pool, signal.mmsi)
    sts_partner = await detect_sts_partner(deps.pool, signal)
    fusion = await run_fusion(deps.adapters, dossier, signal)
    similar = await persistence.fetch_similar_incidents(
        deps.pool,
        f"{signal.zone} {signal.type.value} {dossier.get('flag')}",
        exclude_mmsi=signal.mmsi,
    )

    out: AnalystOutput = await run_analyst(
        signal, merged, dossier,
        sts_partner=sts_partner, fusion=fusion, similar=similar,
    )

    audit = build_audit_block(
        sentinel.incident_id, "analyst",
        node_input={
            "merged_signal_ids": sentinel.merged_signals,
            "dossier_flag": dossier.get("flag"),
            "sts_partner": sts_partner,
        },
        output=out.model_dump(mode="json"),
        prompt_version=analyst_mod.PROMPT_VERSION,
    )

    return {
        "analyst": out.model_dump(mode="json"),
        "audit": [*state.get("audit", []), audit],
    }


async def _node_desk(state: GraphState, deps: Deps) -> dict[str, Any]:
    signal = Signal.model_validate(state["signal"])
    sentinel = SentinelOutput.model_validate(state["sentinel"])
    analyst = AnalystOutput.model_validate(state["analyst"])

    out: DeskOutput = await run_desk(signal.zone, analyst)

    audit = build_audit_block(
        sentinel.incident_id, "desk",
        node_input={"zone": signal.zone, "typology": analyst.typology.value,
                    "severity": analyst.severity},
        output=out.model_dump(mode="json"),
        prompt_version=desk_mod.PROMPT_VERSION,
    )

    return {
        "desk": out.model_dump(mode="json"),
        "audit": [*state.get("audit", []), audit],
    }


def _node_gate(state: GraphState, deps: Deps) -> dict[str, Any]:
    """Assemble the Incident and decide auto-confirm vs human review at tau."""
    signal = Signal.model_validate(state["signal"])
    sentinel = SentinelOutput.model_validate(state["sentinel"])
    analyst = AnalystOutput.model_validate(state["analyst"])
    desk = DeskOutput.model_validate(state["desk"]) if state.get("desk") else None
    merged = [Signal.model_validate(s) for s in state.get("merged_signals", [])] or [signal]

    tau = get_settings().escalation_tau
    # At/above tau we auto-confirm; below tau the incident still exists but is
    # flagged for an analyst's eyes (status OPEN, needs_review).
    confirmed = analyst.severity >= tau
    status = IncidentStatus.CONFIRMED if confirmed else IncidentStatus.OPEN

    incident = Incident(
        id=sentinel.incident_id,
        mmsi=signal.mmsi,
        zone=signal.zone,
        typology=analyst.typology,
        severity=analyst.severity,
        confidence=analyst.confidence,
        status=status,
        opened_at=time.time(),
        position=signal.position,
        summary=analyst.summary,
        market_note=desk.market_note if desk else "",
        signals=merged,
        sentinel=sentinel,
        analyst=analyst,
        desk=desk,
    )

    return {
        "incident": incident.model_dump(mode="json"),
        "needs_review": not confirmed,
    }


async def _node_persist(state: GraphState, deps: Deps) -> dict[str, Any]:
    """Flush: incident row + payload, audit chain, RAG embedding, stream publish."""
    incident = Incident.model_validate(state["incident"])
    audit_rows = [AuditEntry.model_validate(a) for a in state.get("audit", [])]

    await persistence.write_incident(deps.pool, incident)
    await persistence.write_audit_entries(deps.pool, audit_rows)
    await persistence.embed_incident(deps.pool, incident)
    await persistence.publish_incident(deps.redis, incident)
    return {}


async def _node_human_review(state: GraphState, deps: Deps) -> dict[str, Any]:
    """Sub-threshold incidents: persisted OPEN for an analyst, not auto-confirmed.

    Still fully audited and published so the UI shows it in the review queue.
    """
    log.info(
        "Incident %s below tau — queued for human review.",
        state.get("incident", {}).get("id"),
    )
    return await _node_persist(state, deps)


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------

def build_audit_block(
    incident_id: str,
    agent: str,
    node_input: Any,
    output: dict[str, Any],
    prompt_version: str,
) -> dict[str, Any]:
    """One audit row as a json-mode dict, ready to append to state['audit']."""
    return persistence.build_audit_entry(
        incident_id=incident_id,
        agent=agent,
        node_input=node_input,
        output=output,
        model=model_name(),
        prompt_version=prompt_version,
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------

def _route_after_sentinel(state: GraphState) -> str:
    return "drop" if state.get("dropped") else "analyst"


def _route_after_gate(state: GraphState) -> str:
    return "human_review" if state.get("needs_review") else "persist"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_graph(deps: Deps, checkpointer: Any | None = None):
    """Compile the StateGraph. ``checkpointer`` is a Postgres saver in prod, or
    None / an in-memory saver in tests. Nodes are bound to ``deps`` via closures."""
    import inspect

    from langgraph.graph import END, StateGraph

    def _bind(fn):
        """Bind a node fn to deps as an async coroutine so LangGraph awaits it.

        A plain ``lambda s: fn(s, deps)`` over an async ``fn`` returns a *coroutine
        object* (LangGraph then raises "Expected dict, got coroutine"). Wrapping in
        an ``async def`` and awaiting awaitable results fixes both async and the
        one sync node (``_node_gate``) uniformly.
        """
        async def _runner(state: GraphState) -> dict[str, Any]:
            result = fn(state, deps)
            if inspect.isawaitable(result):
                result = await result
            return result

        return _runner

    sg = StateGraph(GraphState)

    sg.add_node("sentinel", _bind(_node_sentinel))
    sg.add_node("analyst", _bind(_node_analyst))
    sg.add_node("desk", _bind(_node_desk))
    sg.add_node("gate", _bind(_node_gate))
    sg.add_node("persist", _bind(_node_persist))
    sg.add_node("human_review", _bind(_node_human_review))

    sg.set_entry_point("sentinel")
    sg.add_conditional_edges(
        "sentinel", _route_after_sentinel,
        {"drop": END, "analyst": "analyst"},
    )
    sg.add_edge("analyst", "desk")
    sg.add_edge("desk", "gate")
    sg.add_conditional_edges(
        "gate", _route_after_gate,
        {"human_review": "human_review", "persist": "persist"},
    )
    sg.add_edge("persist", END)
    sg.add_edge("human_review", END)

    return sg.compile(checkpointer=checkpointer)
