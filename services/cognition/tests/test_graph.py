"""End-to-end graph test on the deterministic (no-Groq) path.

Crafts a DARK_VESSEL + LOITERING pair on one MMSI in Suez and drives the full
machine with no datastores and no Groq key. Asserts the Sentinel coalesces the
pair, the Analyst produces a non-empty reasoning trace, and a complete 3-entry
audit chain (sentinel + analyst + desk) is assembled.

Runs entirely offline: no Postgres, no Redis, no LLM.
"""
from __future__ import annotations

import time

import pytest

from trident_contracts.enums import SignalType, Typology
from trident_contracts.incident import AuditEntry, Incident
from trident_contracts.signal import Signal

from cognition.consumer import process_one
from cognition.graph import Deps, build_graph


def _signal(stype: SignalType, mmsi: int, *, ts: float, severity: float) -> Signal:
    return Signal(
        ts=ts,
        type=stype,
        mmsi=mmsi,
        zone="suez",
        severity=severity,
        confidence=0.8,
        position=(30.55, 32.35),
        evidence={"note": "synthetic test fixture"},
        detector_version="test-0",
    )


@pytest.mark.asyncio
async def test_dark_plus_loitering_produces_audited_incident():
    # No pool, no redis, no checkpointer, no Groq key -> pure deterministic path.
    deps = Deps(pool=None, redis=None)
    graph = build_graph(deps, checkpointer=None)

    now = time.time()
    mmsi = 636019825  # MID 636 -> Liberia (a flag of convenience)
    dark = _signal(SignalType.DARK_VESSEL, mmsi, ts=now - 120, severity=0.7)
    loiter = _signal(SignalType.LOITERING, mmsi, ts=now, severity=0.6)

    # Same thread_id (keyed by zone in the consumer) gives the Sentinel its
    # episodic continuity, so the second signal merges with the first.
    config = {"configurable": {"thread_id": "zone:suez"}}

    await graph.ainvoke({"signal": dark.model_dump(mode="json")}, config=config)
    final = await graph.ainvoke({"signal": loiter.model_dump(mode="json")}, config=config)

    # --- Sentinel coalesced the two firings into one incident ---------------
    assert final["sentinel"]["escalate"] is True
    assert len(final["sentinel"]["merged_signals"]) == 2, "dark+loiter should merge"

    # --- An incident was produced ------------------------------------------
    assert final.get("incident") is not None
    incident = Incident.model_validate(final["incident"])
    assert incident.mmsi == mmsi
    assert incident.zone == "suez"
    assert incident.typology != Typology.BENIGN
    # Dark vessel + flag-of-convenience -> sanctions evasion in the rule cascade.
    assert incident.typology == Typology.SANCTIONS_EVASION

    # --- Analyst left an explicit, non-empty reasoning trace ---------------
    assert incident.analyst is not None
    assert len(incident.analyst.reasoning_trace) >= 2

    # --- Desk attached a dated market note ---------------------------------
    assert incident.desk is not None
    assert incident.market_note

    # --- Full 3-entry audit chain, all deterministic-fallback --------------
    audit = [AuditEntry.model_validate(a) for a in final["audit"]]
    assert [a.agent for a in audit] == ["sentinel", "analyst", "desk"]
    for a in audit:
        assert a.model == "deterministic-fallback"
        assert a.input_hash and len(a.input_hash) == 64   # sha256 hex
        assert a.incident_id == incident.id


@pytest.mark.asyncio
async def test_low_value_signal_is_dropped():
    """A lone low-severity congestion blip is noise — the Sentinel drops it and
    no incident is produced."""
    deps = Deps(pool=None, redis=None)
    graph = build_graph(deps, checkpointer=None)

    blip = _signal(SignalType.CONGESTION, 211111111, ts=time.time(), severity=0.1)
    final = await graph.ainvoke(
        {"signal": blip.model_dump(mode="json")},
        config={"configurable": {"thread_id": "zone:suez-quiet"}},
    )

    assert final["dropped"] is True
    assert final.get("incident") is None
    # Only the sentinel node ran -> a single audit row.
    assert len(final["audit"]) == 1
    assert final["audit"][0]["agent"] == "sentinel"


@pytest.mark.asyncio
async def test_process_one_smoke():
    """The consumer's per-signal entrypoint runs the graph without a checkpointer
    (thread_id is supplied; in-memory invocation works without durability)."""
    deps = Deps(pool=None, redis=None)
    graph = build_graph(deps, checkpointer=None)
    sig = _signal(SignalType.DARK_VESSEL, 636019999, ts=time.time(), severity=0.8)
    # Should not raise.
    await process_one(graph, sig)
