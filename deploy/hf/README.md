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

**This Space is a self-contained demo.** It runs on a *synthetic* AIS feed (a
scripted Suez scenario — a tanker goes dark in the Gulf of Suez, a loitering
partner sets up a ship-to-ship transfer, a U-turn cluster forms) and the
**deterministic** classifier, so no API keys are baked in. Watch the right rail:
an incident is classified and streamed in within a minute, with a reasoning
trace and a market note.

The full system fuses a **live** global AIS feed (AISStream) with a **Groq**
LangGraph agent swarm (Sentinel → Analyst → Desk), TimescaleDB/PostGIS
forensics, and a data-fusion layer (SAR · OFAC sanctions · GDELT · marine
weather · market data).

→ **Source & full multi-service stack:** https://github.com/sidnov6/trident

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

## What's different in this demo vs. the full system

| | This Space | Full stack (GitHub) |
|---|---|---|
| AIS feed | synthetic scripted scenario | live global (AISStream) |
| Cognition | deterministic classifier | Groq LLM swarm |
| Persistence / forensics | in-memory (Redis only) | TimescaleDB + PostGIS replay |
| Data fusion | seams present, off | SAR · OFAC · GDELT · weather · market |

Built with FastAPI, Redis, LangGraph, Next.js, MapLibre + deck.gl, OpenSeaMap.
