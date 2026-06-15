# TRIDENT — Integration Contract (read before building any service)

The **spine is frozen**. Services communicate *only* through Redis and Postgres.
Never import one service's code from another. Import the three shared packages:

| Package | Import name | What it gives you |
|---|---|---|
| `packages/contracts` | `trident_contracts` | `Signal`, `SignalLite`, `VesselState`, `VesselLite`, `VesselDossier`, `Incident`, `SentinelOutput`, `AnalystOutput`, `DeskOutput`, `AuditEntry`, enums, `bucket_for_ship_type`, WS message models |
| `packages/common`    | `trident_common`    | `get_settings()` (env), `keys` (Redis key/stream names) |
| `packages/geo`       | `trident_geo`       | `CHOKEPOINTS`, `CHOKEPOINT_BOXES`, `zone_for_point`, `near_edge`, `flag_for_mmsi`, `load_zone_geojson` |

All services install these editable: `pip install -e packages/contracts -e packages/common -e packages/geo`.

## The event bus (Redis Streams)

- `keys.STREAM_SIGNALS` (`trident:signals`) — **detectors → cognition & api**. Payload: `Signal.to_stream_fields()` (a `{"payload": <json>}` map). Read back with `Signal.from_stream_fields(fields)`.
- `keys.STREAM_INCIDENTS` (`trident:incidents`) — **cognition → api**. Payload field `payload` = `Incident.model_dump_json()`.
- Consumer groups: `keys.CONSUMER_GROUP_COGNITION`, `keys.CONSUMER_GROUP_API`. Use `XREADGROUP` with `MKSTREAM`.

## Redis hot state (written by ingestor, read by api)

- `keys.vessel_key(mmsi)` → HASH of `VesselState` fields. `EXPIRE` = `keys.VESSEL_TTL_S` (1800).
- `keys.zone_geo_key(zone)` → GEO index: `GEOADD chokepoint:{zone}:geo lon lat mmsi`. Viewport + congestion read via `GEOSEARCH`.
- `keys.zone_count_key(zone)`, `keys.zone_baseline_key(zone)` → congestion EWMA state.
- `keys.WATCHLIST_PRIORITY` → SET of priority MMSIs.

## Postgres (schema in services/db/schema.sql)

Tables: `tracks` (hypertable, PostGIS point), `vessels`, `signals`, `incidents` (has `payload` JSONB = full Incident), `audit_log` (append-only), `feed_gaps`, `incident_embeddings`, plus fusion tables `sanctions_vessels`, `sar_scenes`.

## Service responsibilities

- **ingestor** — owns AIS ingest, Redis state, detectors, async track writer, publishes Signals. Also publishes `vessel_delta` source data (api reads Redis state to build deltas). Records `feed_gaps`.
- **cognition** — consumes `STREAM_SIGNALS`, runs LangGraph Sentinel→Analyst→Desk (Groq), persists incidents + audit, publishes to `STREAM_INCIDENTS`.
- **api** — FastAPI. Builds `vessel_delta` (Redis state), relays `signal_tick` (STREAM_SIGNALS) and `incident` (STREAM_INCIDENTS) and `zone_stats` over one WebSocket `/ws`. REST: `/vessels`, `/vessels/{mmsi}` (dossier), `/incidents`, `/zones`, `/health`.
- **replay** — separate FastAPI on :8100. Streams historical `tracks` back at adjustable speed; `ST_DWithin` proximity ("who was near the dark vessel").
- **web** — Next.js command center. Consumes `/ws` + REST. Uses `packages/contracts/ts/contracts.ts` types.

## Hard rules (from the spec, non-negotiable)

1. **No LLM in the per-message loop.** Agents consume Signals, never raw AIS.
2. **Coalesce, don't queue.** Latest-state-wins per MMSI; never an unbounded backlog.
3. **Bounding boxes are `[lat, lon]`.** `trident_geo.validate_boxes()` enforces it.
4. **Audit everything in cognition.** Every agent node writes an `audit_log` row.
5. **Feed gaps are first-class.** Log disconnects to `feed_gaps`.
6. Detectors are pure/versioned (`detector_version`); same input → same output.

## Run modes

- `AIS_SOURCE=synthetic` (default) → ingestor runs a generated Suez scenario, **no API key needed**, and deterministically produces a dark-vessel + loitering + STS event so the whole pipeline is demoable offline.
- `AIS_SOURCE=live` → real AISStream WebSocket (needs `AISSTREAM_API_KEY`).
