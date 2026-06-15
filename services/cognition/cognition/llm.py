"""Groq LLM client wiring for the cognition swarm.

Centralises the model name, temperature and structured-output plumbing so the
three agents share one configuration surface. Critically, this module degrades
gracefully: if no Groq API key is configured (or langchain-groq is not
installed) ``has_llm()`` returns False and the agents fall back to their
deterministic classifiers. The graph therefore runs end-to-end offline.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Optional, Type, TypeVar

from pydantic import BaseModel

from trident_common.settings import get_settings

log = logging.getLogger("cognition.llm")

T = TypeVar("T", bound=BaseModel)

# Low temperature: these are analytic judgements, not creative writing. The Desk
# agent is allowed a touch more latitude for its prose market note.
DEFAULT_TEMPERATURE = 0.1
DESK_TEMPERATURE = 0.3

# Sentinel value recorded in audit rows when the deterministic path runs.
FALLBACK_MODEL = "deterministic-fallback"


@lru_cache(maxsize=1)
def _build_client() -> Optional[Any]:
    """Construct a single shared ChatGroq client, or None if unavailable.

    Cached so we only pay the import + handshake cost once for the resident
    process. Returns None (never raises) when the key is missing or the
    dependency is absent — callers branch on ``has_llm()``.
    """
    settings = get_settings()
    if not settings.groq_api_key:
        log.warning(
            "GROQ_API_KEY not set — cognition will use the deterministic "
            "fallback classifier (model=%s).",
            FALLBACK_MODEL,
        )
        return None
    try:
        from langchain_groq import ChatGroq
    except ImportError:  # pragma: no cover - dependency-optional path
        log.warning(
            "langchain-groq not installed — falling back to deterministic "
            "classifier (model=%s).",
            FALLBACK_MODEL,
        )
        return None
    try:
        return ChatGroq(
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            temperature=DEFAULT_TEMPERATURE,
        )
    except Exception as exc:  # pragma: no cover - misconfiguration guard
        log.warning("Failed to construct ChatGroq (%s) — using fallback.", exc)
        return None


def has_llm() -> bool:
    """True when a live Groq client is available; drives the fallback branch."""
    return _build_client() is not None


def model_name() -> str:
    """The model string recorded in audit rows. Either the Groq model id or the
    deterministic-fallback sentinel."""
    settings = get_settings()
    return settings.groq_model if has_llm() else FALLBACK_MODEL


def structured(model_cls: Type[T], *, temperature: float | None = None) -> Optional[Any]:
    """Return a runnable that yields a validated ``model_cls`` instance.

    Uses ChatGroq's ``.with_structured_output`` so each agent gets a typed
    object back rather than free text. Returns None when no LLM is available,
    signalling the caller to take its deterministic path.
    """
    client = _build_client()
    if client is None:
        return None
    if temperature is not None:
        # ChatGroq is immutable-ish; .bind returns a configured copy.
        client = client.bind(temperature=temperature)
    return client.with_structured_output(model_cls)
