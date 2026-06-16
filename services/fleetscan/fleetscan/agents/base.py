"""FleetAgent interface + the hit it returns."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..memory import AgentMemory, Snapshot


@dataclass
class AgentHit:
    category: str            # ThreatCategory value
    severity: float          # 0..1 how alarming if real
    confidence: float        # 0..1 how sure the rule is
    evidence: list[str] = field(default_factory=list)  # plain-language facts


class FleetAgent:
    """A deterministic per-vessel classifier. classify() is pure + cheap."""

    category: str = ""
    name: str = ""

    def classify(self, s: Snapshot, mem: AgentMemory, now: float) -> AgentHit | None:
        raise NotImplementedError
