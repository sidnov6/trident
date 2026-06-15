# ──────────────────────────────────────────────────────────────────────────
# TRIDENT — single-container demo image (Hugging Face Space).
#
# The production deployment is the multi-service docker-compose stack. This image
# folds the stack into ONE container for a self-contained public demo:
#   redis + ingestor (SYNTHETIC scripted Suez arc) + cognition (deterministic,
#   no keys) + FastAPI serving the built UI + REST + WebSocket on a single port.
# No API keys are baked in — the demo runs on generated AIS data with the
# deterministic classifier, so it is safe to publish.
#
# For the real, live, multi-service system see docker-compose.yml.
# ──────────────────────────────────────────────────────────────────────────

# ---- stage 1: build the Next.js static export ----------------------------
FROM node:20-slim AS webbuilder
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci || npm install
COPY web/ ./
# Static export + same-origin (UI, REST and WS all served from one origin).
ENV HF_EXPORT=1 \
    NEXT_PUBLIC_API_BASE="" \
    NEXT_PUBLIC_REPLAY_BASE=""
RUN npm run build

# ---- stage 2: python runtime ---------------------------------------------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends redis-server curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY packages/ ./packages/
COPY services/ ./services/
RUN pip install --upgrade pip \
    && pip install \
        -e packages/contracts -e packages/common -e packages/geo \
        -e services/ingestor -e services/api -e services/cognition

# the pre-built UI, served by FastAPI on the single exposed port
COPY --from=webbuilder /web/out ./web_static
COPY deploy/hf/start.sh ./start.sh
RUN chmod +x ./start.sh

ENV AIS_SOURCE=synthetic \
    REDIS_URL=redis://localhost:6379/0 \
    DATABASE_URL="" \
    GROQ_API_KEY="" \
    FUSION_ENABLED=false \
    TRIDENT_STATIC_DIR=/app/web_static \
    LOG_LEVEL=INFO \
    PORT=7860

# HF Spaces route to the port declared here / in README app_port
EXPOSE 7860
CMD ["./start.sh"]
