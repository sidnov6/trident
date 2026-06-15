#!/usr/bin/env bash
# Single-container launcher for the TRIDENT Hugging Face demo.
# Brings up redis + ingestor (synthetic) + cognition (deterministic) and then
# runs the FastAPI gateway in the foreground, serving UI + REST + WS on $PORT.
set -u
PORT="${PORT:-7860}"

log() { echo "[trident] $*"; }

log "starting embedded redis..."
redis-server --daemonize yes --save "" --appendonly no --port 6379
# wait for redis to answer
for i in $(seq 1 30); do
  if redis-cli ping >/dev/null 2>&1; then log "redis up"; break; fi
  sleep 1
done

# Tier 0-2: synthetic AIS firehose -> Redis state -> detectors -> signal bus
log "starting ingestor (AIS_SOURCE=${AIS_SOURCE:-synthetic})..."
python -m ingestor.main &
INGESTOR_PID=$!

# Tier 3: cognition swarm (deterministic fallback when no GROQ_API_KEY)
log "starting cognition..."
python -m cognition.main &
COGNITION_PID=$!

# If a background worker dies, surface it but keep the UI/API serving.
trap 'log "shutting down"; kill $INGESTOR_PID $COGNITION_PID 2>/dev/null' EXIT

# Tier 4: FastAPI serves the static UI (TRIDENT_STATIC_DIR) + REST + /ws.
log "starting api on :${PORT} (serving UI + REST + WS)..."
exec uvicorn api.main:app --host 0.0.0.0 --port "${PORT}"
