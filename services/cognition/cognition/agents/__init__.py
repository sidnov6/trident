"""The three agents of the cognition swarm.

Each agent module exposes:
  * a ``PROMPT_VERSION`` constant (audited per node),
  * an async ``run(...)`` entrypoint the graph node calls,
  * a deterministic fallback that runs when no Groq client is available.

Sentinel correlates, Analyst investigates, Desk strategises.
"""
from .analyst import run_analyst
from .desk import run_desk
from .sentinel import run_sentinel

__all__ = ["run_sentinel", "run_analyst", "run_desk"]
