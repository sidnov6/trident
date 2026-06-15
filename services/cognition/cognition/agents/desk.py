"""Desk — the macro strategist.

Takes a confirmed incident and reasons one move ahead: which commodities route
through this chokepoint, the reroute penalty if the lane is disrupted (Suez ->
Cape of Good Hope adds ~10-14 days), tanker/freight rate direction (TD3C, Baltic),
Brent/TTF sensitivity, and war-risk premia. Produces a short, dated market note.

The Desk may use a web-search tool ONLY when ``settings.desk_search_enabled`` and
a Tavily key is present; otherwise it reasons from first principles. Retrieved
facts are kept strictly separate from model inference.

Returns a :class:`DeskOutput`.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

from trident_common.settings import get_settings
from trident_contracts.enums import Typology
from trident_contracts.incident import AnalystOutput, DeskOutput

from ..llm import FALLBACK_MODEL, model_name, structured

log = logging.getLogger("cognition.agents.desk")

PROMPT_VERSION = "desk-v1"

# Per-chokepoint commodity / route knowledge used by the deterministic note and
# injected into the LLM prompt as grounding.
_ZONE_PROFILE: dict[str, dict[str, Any]] = {
    "suez": {
        "label": "Suez Canal",
        "commodities": ["crude oil", "refined products", "LNG", "containerised goods", "grain"],
        "reroute_days": 12.0,
        "reroute_path": "Cape of Good Hope",
        "rate_direction": "TD3C / containerised freight up",
        "brent": "Brent +1-3 USD/bbl on sustained disruption; TTF gas sensitive via LNG",
    },
    "hormuz": {
        "label": "Strait of Hormuz",
        "commodities": ["crude oil", "condensate", "LNG"],
        "reroute_days": None,   # largely un-reroutable; ~20% of seaborne oil
        "reroute_path": "no maritime bypass (limited pipeline relief)",
        "rate_direction": "VLCC / TD3C sharply up",
        "brent": "Brent highly sensitive: +5-10 USD/bbl on credible closure risk",
    },
    "bab_el_mandeb": {
        "label": "Bab-el-Mandeb",
        "commodities": ["crude oil", "refined products", "containerised goods"],
        "reroute_days": 12.0,
        "reroute_path": "Cape of Good Hope",
        "rate_direction": "TD3C / box freight up; war-risk premia rising",
        "brent": "Brent +1-3 USD/bbl; insurance/war-risk the dominant channel",
    },
    "malacca": {
        "label": "Strait of Malacca",
        "commodities": ["crude oil", "LNG", "containerised goods"],
        "reroute_days": 3.5,
        "reroute_path": "Sunda / Lombok Straits",
        "rate_direction": "Asia-bound tanker and box rates up",
        "brent": "Brent +1-2 USD/bbl; strong China/Japan/Korea supply exposure",
    },
    "panama": {
        "label": "Panama Canal",
        "commodities": ["LPG", "containerised goods", "grain", "refined products"],
        "reroute_days": 8.0,
        "reroute_path": "Strait of Magellan / Suez",
        "rate_direction": "USGC-Asia LPG and box rates up",
        "brent": "Limited Brent impact; LPG/USGC freight the main channel",
    },
    "bosphorus": {
        "label": "Bosphorus",
        "commodities": ["crude oil", "refined products", "grain"],
        "reroute_days": None,
        "reroute_path": "no maritime bypass for Black Sea trade",
        "rate_direction": "Aframax / grain freight up",
        "brent": "Brent +1-2 USD/bbl; CPC/Black Sea crude and grain corridor exposure",
    },
}

_DEFAULT_PROFILE: dict[str, Any] = {
    "label": "chokepoint",
    "commodities": ["crude oil", "refined products", "containerised goods"],
    "reroute_days": 10.0,
    "reroute_path": "alternative routing",
    "rate_direction": "freight rates up",
    "brent": "modest Brent sensitivity",
}


class _Note(BaseModel):
    """What the LLM Desk returns; we wrap it into a DeskOutput, keeping retrieved
    facts and inference separate per the contract."""

    market_note: str = ""
    commodities: list[str] = Field(default_factory=list)
    reroute_days: Optional[float] = None
    rate_direction: Optional[str] = None
    brent_sensitivity: Optional[str] = None
    inferences: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """You are the DESK, the macro strategist of a maritime intelligence \
cell. Given a CONFIRMED chokepoint incident, write a short DATED market note on the \
second-order economic shock if this disruption escalates.

Reason about: which commodities transit this chokepoint; the reroute penalty in days \
and path; tanker/freight direction (e.g. TD3C, Baltic indices); Brent and TTF gas \
sensitivity; and war-risk / insurance premia. Be concrete and quantified but \
appropriately hedged.

CRITICAL: keep retrieved facts (from any web search provided) strictly separate from \
your own inference. Put model reasoning in 'inferences'. Write like a sell-side \
strategist desk note — tight, dated, actionable."""


def _today() -> str:
    return dt.date.today().isoformat()


async def _web_search(query: str) -> list[str]:
    """Tavily-backed retrieval, only when enabled and the dependency is present.

    Returns a list of short fact strings (source-attributed). Empty when search
    is disabled or unavailable — the Desk then reasons purely.
    """
    settings = get_settings()
    if not settings.desk_search_enabled or not settings.tavily_api_key:
        return []
    try:
        from langchain_community.tools.tavily_search import TavilySearchResults
    except ImportError:  # pragma: no cover - optional extra
        log.warning("desk_search_enabled but langchain-community/tavily not installed.")
        return []
    try:
        tool = TavilySearchResults(max_results=4, api_key=settings.tavily_api_key)
        results = await tool.ainvoke({"query": query})
        facts: list[str] = []
        for r in results or []:
            content = (r.get("content") or "").strip().replace("\n", " ")
            url = r.get("url", "")
            if content:
                facts.append(f"{content[:240]} [src: {url}]")
        return facts
    except Exception as exc:  # pragma: no cover
        log.warning("Desk web search failed (%s); reasoning without it.", exc)
        return []


def _deterministic_note(
    zone: str,
    typology: Typology,
    analyst: AnalystOutput,
    retrieved_facts: list[str],
) -> DeskOutput:
    """First-principles market note from the per-zone commodity profile."""
    p = _ZONE_PROFILE.get(zone, _DEFAULT_PROFILE)
    commodities = list(p["commodities"])
    reroute_days = p["reroute_days"]
    reroute_path = p["reroute_path"]

    reroute_clause = (
        f"a reroute via {reroute_path} adds ~{reroute_days:.0f} days of steaming"
        if reroute_days is not None
        else f"there is {reroute_path}, so disruption is largely un-substitutable"
    )

    note = (
        f"[{_today()}] {p['label']} — {typology.value.replace('_', ' ').lower()} "
        f"incident (severity {analyst.severity:.2f}, confidence {analyst.confidence:.2f}). "
        f"Key transiting commodities: {', '.join(commodities)}. If transit is impaired, "
        f"{reroute_clause}, tightening tonne-mile demand and pushing {p['rate_direction']}. "
        f"Price channel: {p['brent']}. War-risk and insurance premia bias higher while the "
        f"incident is unresolved. Watch for confirmation before sizing any move."
    )

    inferences = [
        f"{p['label']} carries {', '.join(commodities[:3])}; impairment raises tonne-mile demand.",
        f"Reroute penalty: {reroute_clause}.",
        f"Rate read-through: {p['rate_direction']}.",
        f"Energy price sensitivity: {p['brent']}.",
        "War-risk / insurance premia bias higher until the incident clears.",
    ]
    if typology in (Typology.SANCTIONS_EVASION, Typology.STS_TRANSFER):
        inferences.append(
            "Shadow-fleet / STS activity points to sanctioned-barrel flow rather than a "
            "physical transit closure — read as compliance/enforcement risk, not supply loss."
        )

    return DeskOutput(
        market_note=note,
        commodities=commodities,
        reroute_days=reroute_days,
        rate_direction=p["rate_direction"],
        brent_sensitivity=p["brent"],
        retrieved_facts=retrieved_facts,
        inferences=inferences,
    )


async def run_desk(zone: str, analyst: AnalystOutput) -> DeskOutput:
    """Produce the dated market note for a confirmed incident.

    Always attempts web-grounding first (no-op unless enabled), then either the
    LLM Desk or the deterministic note. Retrieved facts are preserved on the
    output regardless of which path produces the prose.
    """
    typology = analyst.typology
    p = _ZONE_PROFILE.get(zone, _DEFAULT_PROFILE)
    retrieved_facts = await _web_search(
        f"{p['label']} shipping disruption {typology.value} impact freight rates "
        f"crude oil reroute {_today()}"
    )

    runnable = structured(_Note)
    if runnable is None:
        out = _deterministic_note(zone, typology, analyst, retrieved_facts)
        log.info("[desk/%s] note for %s/%s", FALLBACK_MODEL, zone, typology.value)
        return out

    facts_block = "\n".join(f"- {f}" for f in retrieved_facts) or "(no web search performed)"
    user = (
        f"CONFIRMED INCIDENT in {p['label']} ({zone}). Typology {typology.value}, "
        f"severity {analyst.severity:.2f}, confidence {analyst.confidence:.2f}.\n"
        f"Analyst summary: {analyst.summary}\n"
        f"Zone commodity profile (grounding): {p}\n"
        f"RETRIEVED FACTS (web search — cite, keep separate from inference):\n{facts_block}\n"
        f"Today is {_today()}. Write the dated desk note now."
    )
    try:
        note: _Note = await runnable.ainvoke([("system", SYSTEM_PROMPT), ("human", user)])
        log.info("[desk/%s] note for %s/%s", model_name(), zone, typology.value)
    except Exception as exc:  # pragma: no cover
        log.warning("[desk] LLM call failed (%s); using deterministic note.", exc)
        return _deterministic_note(zone, typology, analyst, retrieved_facts)

    return DeskOutput(
        market_note=note.market_note,
        commodities=note.commodities or list(p["commodities"]),
        reroute_days=note.reroute_days if note.reroute_days is not None else p["reroute_days"],
        rate_direction=note.rate_direction or p["rate_direction"],
        brent_sensitivity=note.brent_sensitivity or p["brent"],
        retrieved_facts=retrieved_facts,   # provenance owned by the tool, not the model
        inferences=note.inferences,
    )
