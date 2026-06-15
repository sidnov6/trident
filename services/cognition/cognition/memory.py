"""The cognition swarm's three memories.

1. **Episodic** — a short per-zone window of recent signals, held inside the
   graph state. Gives the Sentinel the local context it needs to coalesce a
   DARK_VESSEL + LOITERING + REROUTE burst on one MMSI into a single incident.

2. **Dossier** — a per-MMSI cache (Redis, TTL'd) of a vessel's static identity,
   recent track and prior-incident ids. Lets the Analyst enrich without
   re-querying Postgres on every signal.

3. **Incident-RAG** — institutional memory. Each confirmed incident summary is
   embedded and stored in ``incident_embeddings``; retrieval is cosine
   similarity computed in Python (no pgvector dependency, per the schema).

Embeddings use a dependency-free hashing/TF scheme so the whole thing works
with **no external embedding API**. The vector is deterministic and stable, and
a real sentence-embedding model can be swapped in later behind ``embed_text``
without touching the storage format.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from collections import deque
from typing import Any, Deque, Optional

from trident_contracts.signal import Signal

log = logging.getLogger("cognition.memory")

# ---------------------------------------------------------------------------
# Episodic memory — per-zone recent-signal window (in-graph)
# ---------------------------------------------------------------------------

EPISODIC_WINDOW_S = 900          # 15 min — the correlation horizon
EPISODIC_MAX_PER_ZONE = 64       # bound the buffer regardless of traffic


class EpisodicBuffer:
    """A bounded, time-windowed buffer of recent signals for one zone.

    The Sentinel pushes each incoming signal and reads back the recent slice for
    the same MMSI to decide whether this is a *new* incident or another facet of
    one already in flight. Kept tiny and serialisable so it can live on the
    LangGraph state (and thus be checkpointed)."""

    def __init__(self, window_s: int = EPISODIC_WINDOW_S) -> None:
        self.window_s = window_s
        self._signals: Deque[Signal] = deque(maxlen=EPISODIC_MAX_PER_ZONE)

    def add(self, signal: Signal) -> None:
        self._signals.append(signal)

    def recent(self, *, now: float | None = None) -> list[Signal]:
        """All buffered signals within the window, newest last."""
        now = now if now is not None else time.time()
        return [s for s in self._signals if now - s.ts <= self.window_s]

    def recent_for_mmsi(self, mmsi: int, *, now: float | None = None) -> list[Signal]:
        return [s for s in self.recent(now=now) if s.mmsi == mmsi]

    # --- serialisation so the buffer can ride on checkpointed graph state ---
    def to_list(self) -> list[dict[str, Any]]:
        return [s.model_dump(mode="json") for s in self._signals]

    @classmethod
    def from_list(cls, raw: list[dict[str, Any]] | None) -> "EpisodicBuffer":
        buf = cls()
        for item in raw or []:
            try:
                buf._signals.append(Signal.model_validate(item))
            except Exception:  # pragma: no cover - tolerate stale shapes
                continue
        return buf


# ---------------------------------------------------------------------------
# Dossier memory — per-MMSI cache in Redis (TTL'd)
# ---------------------------------------------------------------------------

DOSSIER_TTL_S = 600
_DOSSIER_KEY = "cognition:dossier:{mmsi}"


class DossierCache:
    """Thin async wrapper over Redis for caching assembled vessel dossiers.

    The Analyst assembles a dossier (static + track + prior incidents) once and
    caches it for ``DOSSIER_TTL_S`` so a burst of signals on the same MMSI does
    not hammer Postgres. Redis is optional: if the client is None the cache is a
    no-op and the Analyst always rebuilds.
    """

    def __init__(self, redis: Any | None) -> None:
        self._redis = redis

    async def get(self, mmsi: int) -> Optional[dict[str, Any]]:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(_DOSSIER_KEY.format(mmsi=mmsi))
        except Exception as exc:  # pragma: no cover - cache must never be fatal
            log.debug("dossier cache get failed: %s", exc)
            return None
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def set(self, mmsi: int, dossier: dict[str, Any]) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(
                _DOSSIER_KEY.format(mmsi=mmsi),
                json.dumps(dossier, default=str),
                ex=DOSSIER_TTL_S,
            )
        except Exception as exc:  # pragma: no cover
            log.debug("dossier cache set failed: %s", exc)


# ---------------------------------------------------------------------------
# Incident-RAG — dependency-free embedding + cosine retrieval
# ---------------------------------------------------------------------------

EMBED_DIM = 256
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def embed_text(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Deterministic hashing/TF embedding — no external API required.

    Tokenises, hashes each token into one of ``dim`` buckets (signed by a second
    hash bit for the hashing-trick), accumulates a term-frequency vector and
    L2-normalises. Stable across processes and good enough for "have we seen a
    case like this before?" nearest-neighbour recall. Swap in a real embedding
    model here later without changing the storage format.
    """
    vec = [0.0] * dim
    tokens = _TOKEN_RE.findall(text.lower())
    for tok in tokens:
        h = hashlib.blake2b(tok.encode(), digest_size=8).digest()
        bucket = int.from_bytes(h[:4], "big") % dim
        sign = 1.0 if (h[4] & 1) else -1.0
        vec[bucket] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two (assumed same-length) vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def top_k_similar(
    query_vec: list[float],
    candidates: list[dict[str, Any]],
    k: int = 3,
    *,
    min_score: float = 0.05,
) -> list[dict[str, Any]]:
    """Rank ``candidates`` (each with an ``embedding`` field) by cosine to the
    query vector. Returns the top-k with a ``score`` attached, filtering out
    near-orthogonal noise below ``min_score``."""
    scored: list[dict[str, Any]] = []
    for cand in candidates:
        emb = cand.get("embedding")
        if not emb:
            continue
        score = cosine(query_vec, emb)
        if score >= min_score:
            scored.append({**cand, "score": round(score, 4)})
    scored.sort(key=lambda c: c["score"], reverse=True)
    return scored[:k]
