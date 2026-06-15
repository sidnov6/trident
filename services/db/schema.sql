-- TRIDENT persistence schema. Loaded once on first Postgres boot
-- (mounted into /docker-entrypoint-initdb.d). Idempotent where possible.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ---------------------------------------------------------------------------
-- tracks: every fix, forever. Hypertable partitioned on ts.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tracks (
  ts         TIMESTAMPTZ NOT NULL,
  mmsi       BIGINT NOT NULL,
  geom       GEOMETRY(Point, 4326),
  sog        REAL,
  cog        REAL,
  heading    REAL,
  nav_status SMALLINT,
  zone       TEXT
);
SELECT create_hypertable('tracks', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS tracks_geom_idx ON tracks USING GIST (geom);
CREATE INDEX IF NOT EXISTS tracks_mmsi_ts_idx ON tracks (mmsi, ts DESC);

-- ---------------------------------------------------------------------------
-- vessels: latest known static identity
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vessels (
  mmsi        BIGINT PRIMARY KEY,
  imo         BIGINT,
  name        TEXT,
  ship_type   SMALLINT,
  flag        TEXT,
  destination TEXT,
  draught     REAL,
  length      REAL,
  beam        REAL,
  updated_at  TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- signals: every detector firing (deterministic evidence trail)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
  id               UUID PRIMARY KEY,
  ts               TIMESTAMPTZ NOT NULL,
  type             TEXT NOT NULL,
  mmsi             BIGINT,
  zone             TEXT,
  severity         REAL,
  confidence       REAL,
  geom             GEOMETRY(Point, 4326),
  evidence         JSONB,
  detector_version TEXT
);
CREATE INDEX IF NOT EXISTS signals_mmsi_ts_idx ON signals (mmsi, ts DESC);
CREATE INDEX IF NOT EXISTS signals_zone_ts_idx ON signals (zone, ts DESC);

-- ---------------------------------------------------------------------------
-- incidents: the case file
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS incidents (
  id          UUID PRIMARY KEY,
  mmsi        BIGINT,
  zone        TEXT,
  typology    TEXT,
  severity    REAL,
  confidence  REAL,
  status      TEXT,                 -- open | confirmed | dismissed | actioned
  opened_at   TIMESTAMPTZ,
  lat         DOUBLE PRECISION,
  lon         DOUBLE PRECISION,
  summary     TEXT,
  market_note TEXT,
  payload     JSONB                 -- full Incident object (signals + agent outputs)
);
CREATE INDEX IF NOT EXISTS incidents_mmsi_idx ON incidents (mmsi);
CREATE INDEX IF NOT EXISTS incidents_opened_idx ON incidents (opened_at DESC);

-- ---------------------------------------------------------------------------
-- audit_log: immutable reasoning provenance (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
  id             BIGSERIAL PRIMARY KEY,
  incident_id    UUID,
  agent          TEXT,
  input_hash     TEXT,
  output         JSONB,
  model          TEXT,
  prompt_version TEXT,
  ts             TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_incident_idx ON audit_log (incident_id, ts);

-- ---------------------------------------------------------------------------
-- feed_gaps: disconnections are first-class intelligence
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS feed_gaps (
  id         BIGSERIAL PRIMARY KEY,
  started_at TIMESTAMPTZ NOT NULL,
  ended_at   TIMESTAMPTZ,
  reason     TEXT
);

-- ---------------------------------------------------------------------------
-- incident_embeddings: long-term incident RAG (institutional memory)
-- Stored as JSONB vector to avoid a hard pgvector dependency; cosine done in app.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS incident_embeddings (
  incident_id UUID PRIMARY KEY,
  mmsi        BIGINT,
  typology    TEXT,
  embedding   JSONB,
  summary     TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- ===========================================================================
-- FUSION LAYER (structured now, populated later). These tables give the
-- Analyst its join targets: MMSI/IMO (sanctions), space-time (SAR), weather.
-- ===========================================================================

-- OFAC / OpenSanctions consolidated maritime entries (MMSI/IMO join key)
CREATE TABLE IF NOT EXISTS sanctions_vessels (
  id          BIGSERIAL PRIMARY KEY,
  imo         BIGINT,
  mmsi        BIGINT,
  name        TEXT,
  flag        TEXT,
  former_flag TEXT,
  aliases     JSONB,
  program     TEXT,                 -- e.g. OFAC SDN, EU, UN
  owner       TEXT,
  source      TEXT,
  loaded_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sanctions_imo_idx ON sanctions_vessels (imo);
CREATE INDEX IF NOT EXISTS sanctions_mmsi_idx ON sanctions_vessels (mmsi);

-- SAR scene catalogue hits (space-time join key — confirms dark vessels)
CREATE TABLE IF NOT EXISTS sar_scenes (
  id          BIGSERIAL PRIMARY KEY,
  scene_id    TEXT,
  mission     TEXT,                 -- Sentinel-1A/C/D
  acquired_at TIMESTAMPTZ,
  footprint   GEOMETRY(Polygon, 4326),
  query_lat   DOUBLE PRECISION,
  query_lon   DOUBLE PRECISION,
  catalog_url TEXT,
  matched_incident UUID,
  created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sar_footprint_idx ON sar_scenes USING GIST (footprint);
