#!/usr/bin/env bash
# Editable-install the TRIDENT spine packages and every Python service into the
# current environment (use a venv: `python -m venv .venv && source .venv/bin/activate`).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "→ installing spine packages (editable)"
pip install -e packages/contracts -e packages/common -e packages/geo

echo "→ installing python services (editable, pulls their runtime deps)"
pip install -e services/ingestor
pip install -e services/api
pip install -e services/replay
# cognition pulls langgraph/langchain-groq; install last as it's the heaviest
pip install -e services/cognition

echo "→ optional dev tooling"
pip install pytest pytest-asyncio

echo
echo "✓ done. Bring up Redis + Postgres(Timescale/PostGIS), copy .env.example → .env,"
echo "  then: make counts   (synthetic feed, no AIS key needed)"
