"""Incident + agent-assessment models — the case file the cognition swarm builds."""
from __future__ import annotations

import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field

from .enums import IncidentStatus, Typology
from .signal import Signal


def _new_id() -> str:
    return str(uuid.uuid4())


class SentinelOutput(BaseModel):
    """Correlator verdict — turns a stream of signals into a coherent incident."""

    incident_id: str = Field(default_factory=_new_id)
    mmsi: int
    zone: str
    merged_signals: list[str] = Field(default_factory=list)  # signal ids
    escalate: bool = False
    rationale: str = ""


class AnalystOutput(BaseModel):
    """Investigator assessment — classified threat with an explicit reasoning trace."""

    typology: Typology = Typology.BENIGN
    severity: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    summary: str = ""
    reasoning_trace: list[str] = Field(default_factory=list)
    # fusion enrichment (structured now, populated later)
    sts_partner_mmsi: Optional[int] = None
    sanctions_match: Optional[dict[str, Any]] = None   # OFAC/OpenSanctions hit
    sar_confirmation: Optional[dict[str, Any]] = None  # Sentinel-1 scene ref
    weather_context: Optional[dict[str, Any]] = None   # Open-Meteo conditions
    osint_context: Optional[dict[str, Any]] = None     # GDELT events nearby


class DeskOutput(BaseModel):
    """Macro strategist note — the second-order economic shock."""

    market_note: str = ""
    commodities: list[str] = Field(default_factory=list)
    reroute_days: Optional[float] = None
    rate_direction: Optional[str] = None   # e.g. "TD3C up"
    brent_sensitivity: Optional[str] = None
    retrieved_facts: list[str] = Field(default_factory=list)  # web-search sourced
    inferences: list[str] = Field(default_factory=list)       # model reasoning


class Incident(BaseModel):
    id: str = Field(default_factory=_new_id)
    mmsi: int
    zone: str
    typology: Typology = Typology.BENIGN
    severity: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: IncidentStatus = IncidentStatus.OPEN
    opened_at: float = 0.0                 # epoch seconds
    position: Optional[tuple[float, float]] = None
    summary: str = ""
    market_note: str = ""

    # full provenance chain (kept on the object for UI; also persisted per-table)
    signals: list[Signal] = Field(default_factory=list)
    sentinel: Optional[SentinelOutput] = None
    analyst: Optional[AnalystOutput] = None
    desk: Optional[DeskOutput] = None


class AuditEntry(BaseModel):
    """Immutable reasoning-provenance row. Append-only."""

    incident_id: str
    agent: str                     # sentinel | analyst | desk
    input_hash: str
    output: dict[str, Any]
    model: str
    prompt_version: str
    ts: float
