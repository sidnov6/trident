---
title: TRIDENT — Maritime Chokepoint Intelligence
emoji: 🔱
colorFrom: indigo
colorTo: red
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Real-time maritime chokepoint intelligence command center.
---

# 🔱 TRIDENT — Maritime Chokepoint Intelligence

A live command center that watches the world's maritime chokepoints, runs an
always-on swarm of deterministic detectors over an AIS firehose, and escalates
anomalies through a chain of reasoning agents that classify the threat and
quantify its economic shockwave.

**This Space runs the real system — no synthetic data.** A live global AIS feed
(AISStream) streams real vessels through six maritime chokepoints; deterministic
detectors watch every message; and a **Groq** LangGraph agent swarm
(Sentinel → Analyst → Desk) classifies anomalies and quantifies their economic
shock. Incidents are persisted to TimescaleDB/PostGIS for the case file and
forensic replay. Because it is live, the map fills with genuine traffic and
incidents appear when real vessels actually behave anomalously — the feed is
honest, not staged.

Everything runs in one container: Postgres (Timescale + PostGIS) + Redis +
ingestor (live) + cognition (Groq) + replay + a FastAPI gateway serving the UI,
REST and WebSocket on a single origin. API keys are injected as encrypted
Hugging Face **Space secrets**, never baked into the image.

→ **Source & multi-service stack:** https://github.com/sidnov6/trident

## Architecture (two-speed cognition)

```
AIS firehose → [FAST LANE] deterministic detectors → Signals (Redis Streams)
                                                          │ wakes the brain only on a firing
                          [SLOW LANE] LangGraph swarm → classified, audited incidents → UI
```

Deterministic reflexes never stop and never call an LLM; cognition is reserved
for the rare moments that earn it. Every incident carries a full provenance
chain — the signals, the reasoning, the timestamps — to a standard an
investigation bureau would accept.

## This Space vs. the multi-host stack

| | This Space (single container) | Multi-host stack (GitHub) |
|---|---|---|
| AIS feed | **live global (AISStream)** | live global (AISStream) |
| Cognition | **Groq LLM swarm** | Groq LLM swarm |
| Persistence / forensics | **TimescaleDB + PostGIS** (ephemeral) | TimescaleDB + PostGIS (durable) |
| Data fusion | seams present, off | SAR · OFAC · GDELT · weather · market |

The only material differences are that storage is ephemeral (it resets when the
Space restarts) and the data-fusion adapters are off. Built with FastAPI, Redis,
LangGraph, Next.js, MapLibre + deck.gl, OpenSeaMap.
