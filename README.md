# TRIDENT — Maritime Chokepoint Intelligence Platform

> Tracking, Reconnaissance & Interdiction of Dark-vessel & Economic Network Threats

A live command center that watches the world's maritime chokepoints in real time, runs
an always-on swarm of deterministic detectors over a global AIS firehose, and escalates
anomalies through a chain of reasoning agents that classify the threat and quantify its
economic shockwave.

Six chokepoints — **Suez Canal** (flagship), Strait of Hormuz, Bab-el-Mandeb, Strait of
Malacca, Panama Canal, Bosphorus.

---

## The core principle: two-speed cognition

The spine of the entire system. **Reflexes are cheap and constant; cognition is expensive
and selective.**

```
AIS FIREHOSE (hundreds of msgs/sec)
        │
  ┌─────▼─────────────────────────┐
  │ FAST LANE · Tier 0–2 REFLEX    │  sub-ms, every message, no LLM
  │ ingest → state → detectors     │
  └─────┬──────────────────────────┘
        │ emits typed Signals
   ┌────▼────┐
   │ EVENT BUS│  Redis Streams — wakes the brain only when something fires
   └────┬────┘
  ┌─────▼──────────────────────────┐
  │ SLOW LANE · Tier 3 COGNITION    │  seconds, event-triggered, always resident
  │ LangGraph swarm (Groq)          │  Sentinel → Analyst → Desk
  └─────┬──────────────────────────┘
        │ confirmed incidents + market notes
   Tier 4 · CASE STORE + UI PUSH
```

We do **all the watching in deterministic code** and reserve the thinking for the rare
moments that earn it. An agent call inside the per-message loop would break the principle —
agents consume **Signals**, never raw messages.

---

## Topology

Five backend services + one frontend + three datastores. Services communicate **only**
through Redis and Postgres, so any one can restart without bringing the others down.

| Service | Tier | Role |
|---|---|---|
| `services/ingestor` | 0–2 | AIS ingest, Redis state engine, detector suite, async track writer |
| `services/cognition` | 3 | LangGraph Sentinel→Analyst→Desk (Groq), audit, incident RAG |
| `services/api` | 4 | FastAPI REST + one multiplexed WebSocket to the UI |
| `services/replay` | 4 | Forensic track replay + `ST_DWithin` proximity ("who was near the dark vessel") |
| `web` | UI | Next.js command center — MapLibre + OpenSeaMap + deck.gl |

Shared, frozen **spine** (importable packages):

| Package | Import | Contents |
|---|---|---|
| `packages/contracts` | `trident_contracts` | Signal / Vessel / Incident / WS schemas (Pydantic) + `ts/contracts.ts` mirror |
| `packages/common` | `trident_common` | `get_settings()`, Redis key + stream names |
| `packages/geo` | `trident_geo` | chokepoint boxes, Suez zones, flag-from-MMSI, geofencing |

See [`docs/INTEGRATION.md`](docs/INTEGRATION.md) for the full inter-service contract and
[`docs/FUSION.md`](docs/FUSION.md) for the data-fusion layer (SAR / OFAC / GDELT / weather / market).

---

## Quickstart

### 1. Configure
```bash
cp .env.example .env
# Runs OUT OF THE BOX with no keys — AIS_SOURCE=synthetic replays a scripted Suez scenario.
# To go live later: set AISSTREAM_API_KEY + AIS_SOURCE=live, and GROQ_API_KEY for real cognition.
```

### 2a. Everything in Docker (recommended)
```bash
docker compose up --build
# web:      http://localhost:3000   ← the command center
# api:      http://localhost:8000   (REST + ws://localhost:8000/ws)
# replay:   http://localhost:8100
```
The first boot loads `services/db/schema.sql` (TimescaleDB hypertable + PostGIS + fusion
tables) automatically.

### 2b. Local dev without Docker
You still need Redis + Postgres(Timescale/PostGIS) running somewhere (point `.env` at them).
Then:
```bash
./scripts/dev_install.sh          # editable-installs the 3 spine packages + service deps
make ingestor                     # synthetic feed → Redis state → detectors → bus
make api                          # FastAPI on :8000
make cognition                    # LangGraph brain on the bus
make web                          # Next.js on :3000
```
Prove the fast lane with no UI:
```bash
make counts                       # live per-zone vessel counts (M1 proof)
```

---

## What "done" looks like (v1 definition)

Open the app → live ships glide through the Suez Canal on a real OpenSeaMap seamark chart →
a tanker goes dark in the Gulf of Suez → within seconds an incident card streams into the
right rail with a full reasoning trace, a threat classification, and an estimated
freight-rate impact. In **synthetic mode this happens on its own** — the harness scripts a
dark-vessel + loitering STS pair + a U-turn cluster so the whole chain is demoable offline.

---

## Milestone map (spec §15) → where it lives

| Milestone | Status | Where |
|---|---|---|
| M0 Skeleton & infra | ✅ | `docker-compose.yml`, `packages/*`, `services/db/schema.sql` |
| M1 Live ingest + state | ✅ | `services/ingestor` (`make counts`) |
| M2 Map MVP | ✅ | `web` (MapLibre + OpenSeaMap + deck.gl IconLayer) |
| M3 Smooth motion | ✅ | `web/lib/ws.ts` dead-reckoning + TripsLayer |
| M4 Reflexes | ✅ | `services/ingestor/ingestor/detectors/*` |
| M5 Cognition | ✅ | `services/cognition` (Groq + deterministic fallback) |
| M6 Cases & audit | ✅ | `incidents` + `audit_log`, dossier panel, threat strip |
| M7 Forensics | ✅ | `services/replay` (`ST_DWithin`, scrubber) |
| M8 Hardening | ◐ | reconnect/backpressure + all detectors in place; per-zone tuning ongoing |

## Non-negotiables (the bureau-grade requirements)

1. **Provenance on every claim** — each incident links to its exact signals, track points, and agent reasoning.
2. **Immutable audit log** — every agent node writes an append-only `audit_log` row (`Signal → Sentinel → Analyst → Desk` is fully reconstructable).
3. **Feed-gap accounting** — disconnections are recorded in `feed_gaps`; you can prove when you went blind.
4. **Replay / chain-of-custody** — any past window is reproducible from the `tracks` hypertable.
5. **Deterministic core** — detectors are pure, versioned functions; the LLM interprets, it never invents the facts.

## Tests

```bash
PYTHONPATH=packages/contracts:packages/common:packages/geo:services/ingestor \
  python -m pytest services/ingestor/tests -q      # 17 detector/maths tests
# cognition graph tests need pytest-asyncio: pip install pytest-asyncio
```
