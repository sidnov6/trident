#!/usr/bin/env bash
# Single-container launcher for the TRIDENT LIVE Hugging Face Space.
# Brings up Postgres (Timescale+PostGIS) + Redis, then the real pipeline:
# ingestor (live AISStream) + cognition (Groq) + replay, and finally the
# FastAPI gateway in the foreground serving UI + REST + WS on $PORT.
#
# Resilient by design: if Postgres fails to initialise, the services degrade to
# Redis-only (live vessels + streamed incidents still work) rather than dying.
set -u
PORT="${PORT:-7860}"
PGVER=16
PGBIN="/usr/lib/postgresql/${PGVER}/bin"
# Use a clean, self-managed data dir initialised at runtime (the container is
# ephemeral). Avoids any reliance on Debian's pre-created cluster + wrappers.
PGDATA="/var/lib/postgresql/data"

log() { echo "[trident] $*"; }
pg() { su postgres -c "$*"; }   # run a command as the postgres user

# ── Postgres (best-effort: failure -> Redis-only degraded mode) ─────────────
start_postgres() {
  mkdir -p "${PGDATA}" /var/run/postgresql
  chown -R postgres:postgres "${PGDATA}" /var/run/postgresql

  if [ ! -s "${PGDATA}/PG_VERSION" ]; then
    log "initialising Postgres at ${PGDATA}..."
    pg "${PGBIN}/initdb -D ${PGDATA} --encoding=UTF8 --auth-local=trust --auth-host=scram-sha-256" || return 1
  fi

  # TimescaleDB needs preload; bind localhost; listen on the default port.
  {
    echo "shared_preload_libraries = 'timescaledb'"
    echo "listen_addresses = 'localhost'"
    echo "port = 5432"
    echo "unix_socket_directories = '/var/run/postgresql'"
  } >> "${PGDATA}/postgresql.conf"

  log "starting Postgres..."
  pg "${PGBIN}/pg_ctl -D ${PGDATA} -w -t 60 start" || return 1

  for i in $(seq 1 30); do
    pg "${PGBIN}/pg_isready -q" && break
    sleep 1
  done

  log "ensuring role + database + schema..."
  # Setup connects over the local socket (trust); services connect via TCP (scram).
  pg "${PGBIN}/psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='trident'\"" | grep -q 1 \
    || pg "${PGBIN}/psql -c \"CREATE ROLE trident LOGIN SUPERUSER PASSWORD 'trident'\""
  pg "${PGBIN}/psql -tAc \"SELECT 1 FROM pg_database WHERE datname='trident'\"" | grep -q 1 \
    || pg "${PGBIN}/psql -c \"CREATE DATABASE trident OWNER trident\""
  pg "${PGBIN}/psql -d trident -v ON_ERROR_STOP=0 -f /app/services/db/schema.sql" \
    && log "schema loaded" || log "schema load reported issues (continuing)"
}

if start_postgres; then
  log "Postgres up."
else
  log "WARNING: Postgres unavailable — services will run Redis-only (degraded)."
  export DATABASE_URL=""
fi

# ── Redis ────────────────────────────────────────────────────────────────
log "starting redis..."
redis-server --daemonize yes --save "" --appendonly no --port 6379
for i in $(seq 1 30); do redis-cli ping >/dev/null 2>&1 && break; sleep 1; done

# ── live pipeline ──────────────────────────────────────────────────────────
log "AIS source: ${AIS_SOURCE:-live}; LLM: ${GROQ_API_KEY:+groq}${GROQ_API_KEY:-deterministic}"
log "starting ingestor (live AISStream)..."
python -m ingestor.main &
INGESTOR_PID=$!
log "starting cognition (Groq swarm)..."
python -m cognition.main &
COGNITION_PID=$!
log "starting replay (forensics, internal :8100)..."
uvicorn replay.main:app --host 0.0.0.0 --port 8100 &
REPLAY_PID=$!

trap 'log "shutting down"; kill $INGESTOR_PID $COGNITION_PID $REPLAY_PID 2>/dev/null' EXIT

# ── gateway (foreground) — serves UI + REST + WS on the single exposed port ──
log "starting api on :${PORT} (UI + REST + WS)..."
exec uvicorn api.main:app --host 0.0.0.0 --port "${PORT}"
