"""Persistence + the append-only audit chain.

Three responsibilities, all on the way *out* of the graph:
  1. Write the full :class:`Incident` to the ``incidents`` table (with the whole
     object in the ``payload`` JSONB column for the UI / dossier panel).
  2. Publish the incident to ``keys.STREAM_INCIDENTS`` so the api relays it to
     the command-center over the WebSocket.
  3. Write one ``audit_log`` row PER NODE (sentinel / analyst / desk), each with
     a sha256 hash of that node's *input*, its output JSONB, the model and the
     prompt version. Audit is non-negotiable and append-only.

Also embeds the confirmed incident into ``incident_embeddings`` for institutional
memory (incident-RAG). Every write is fail-soft logged but the audit write is the
last thing we ever skip — provenance first.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Optional

from trident_common import keys
from trident_contracts.incident import AuditEntry, Incident

from .memory import embed_text

log = logging.getLogger("cognition.persistence")


def input_hash(payload: Any) -> str:
    """sha256 over a canonical JSON encoding of a node's input — the audit anchor."""
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def build_audit_entry(
    incident_id: str,
    agent: str,
    node_input: Any,
    output: dict[str, Any],
    model: str,
    prompt_version: str,
) -> AuditEntry:
    """Construct one immutable provenance row for a single agent node."""
    return AuditEntry(
        incident_id=incident_id,
        agent=agent,
        input_hash=input_hash(node_input),
        output=output,
        model=model,
        prompt_version=prompt_version,
        ts=time.time(),
    )


async def write_audit_entries(pool: Any | None, entries: list[AuditEntry]) -> None:
    """Append the audit chain. Append-only: INSERT, never UPDATE/DELETE."""
    if pool is None or not entries:
        if pool is None:
            log.warning("No DB pool — audit chain (%d rows) not persisted (offline).", len(entries))
        return
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO audit_log
                (incident_id, agent, input_hash, output, model, prompt_version, ts)
            VALUES ($1::uuid, $2, $3, $4::jsonb, $5, $6, to_timestamp($7))
            """,
            [
                (
                    e.incident_id,
                    e.agent,
                    e.input_hash,
                    json.dumps(e.output, default=str),
                    e.model,
                    e.prompt_version,
                    e.ts,
                )
                for e in entries
            ],
        )
    log.info("Wrote %d audit rows for incident %s", len(entries), entries[0].incident_id)


async def write_incident(pool: Any | None, incident: Incident) -> None:
    """Upsert the incident row, full object in ``payload`` JSONB."""
    if pool is None:
        log.warning("No DB pool — incident %s not persisted (offline).", incident.id)
        return
    lat = incident.position[0] if incident.position else None
    lon = incident.position[1] if incident.position else None
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO incidents
                (id, mmsi, zone, typology, severity, confidence, status,
                 opened_at, lat, lon, summary, market_note, payload)
            VALUES ($1::uuid, $2, $3, $4, $5, $6, $7,
                    to_timestamp($8), $9, $10, $11, $12, $13::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                typology = EXCLUDED.typology,
                severity = EXCLUDED.severity,
                confidence = EXCLUDED.confidence,
                status = EXCLUDED.status,
                summary = EXCLUDED.summary,
                market_note = EXCLUDED.market_note,
                payload = EXCLUDED.payload
            """,
            incident.id,
            incident.mmsi,
            incident.zone,
            incident.typology.value,
            incident.severity,
            incident.confidence,
            incident.status.value,
            incident.opened_at or time.time(),
            lat,
            lon,
            incident.summary,
            incident.market_note,
            incident.model_dump_json(),
        )
    log.info("Persisted incident %s (%s/%s)", incident.id, incident.zone, incident.typology.value)


async def embed_incident(pool: Any | None, incident: Incident) -> None:
    """Store the incident's summary embedding for incident-RAG retrieval."""
    if pool is None:
        return
    text = " ".join(
        filter(None, [incident.typology.value, incident.zone, incident.summary])
    )
    vec = embed_text(text)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO incident_embeddings
                    (incident_id, mmsi, typology, embedding, summary)
                VALUES ($1::uuid, $2, $3, $4::jsonb, $5)
                ON CONFLICT (incident_id) DO UPDATE SET
                    embedding = EXCLUDED.embedding,
                    summary = EXCLUDED.summary
                """,
                incident.id,
                incident.mmsi,
                incident.typology.value,
                json.dumps(vec),
                incident.summary,
            )
    except Exception as exc:  # pragma: no cover - non-critical memory write
        log.debug("incident embedding write skipped (%s)", exc)


async def fetch_similar_incidents(
    pool: Any | None,
    summary_text: str,
    *,
    exclude_mmsi: Optional[int] = None,
    k: int = 3,
) -> list[dict[str, Any]]:
    """Retrieve the k most similar prior incidents by cosine over stored vectors.

    Cosine is computed in Python (the schema deliberately avoids pgvector). Reads
    a bounded recent slice of embeddings and ranks them against the query text.
    """
    if pool is None:
        return []
    from .memory import top_k_similar  # local import keeps module import light

    query_vec = embed_text(summary_text)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT incident_id::text, mmsi, typology, summary, embedding
                FROM incident_embeddings
                ORDER BY created_at DESC
                LIMIT 500
                """
            )
    except Exception as exc:  # pragma: no cover
        log.debug("similar-incident fetch skipped (%s)", exc)
        return []

    candidates: list[dict[str, Any]] = []
    for r in rows:
        if exclude_mmsi is not None and r["mmsi"] == exclude_mmsi:
            continue
        emb = r["embedding"]
        if isinstance(emb, str):
            try:
                emb = json.loads(emb)
            except json.JSONDecodeError:
                continue
        candidates.append(
            {
                "incident_id": r["incident_id"],
                "mmsi": r["mmsi"],
                "typology": r["typology"],
                "summary": r["summary"],
                "embedding": emb,
            }
        )
    return top_k_similar(query_vec, candidates, k=k)


async def publish_incident(redis: Any | None, incident: Incident) -> None:
    """Publish to ``keys.STREAM_INCIDENTS`` (field ``payload`` = Incident JSON)."""
    if redis is None:
        log.warning("No Redis — incident %s not published (offline).", incident.id)
        return
    await redis.xadd(keys.STREAM_INCIDENTS, {"payload": incident.model_dump_json()})
    log.info("Published incident %s to %s", incident.id, keys.STREAM_INCIDENTS)
