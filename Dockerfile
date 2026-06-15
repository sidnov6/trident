# ──────────────────────────────────────────────────────────────────────────
# TRIDENT — single-container LIVE stack for the Hugging Face Space.
#
# Folds the full multi-service system into ONE container running the REAL
# pipeline (no synthetic data):
#   Postgres 16 (TimescaleDB + PostGIS) + Redis
#   + ingestor (LIVE AISStream)  + cognition (Groq LangGraph swarm)
#   + replay (forensics)         + FastAPI serving the UI + REST + WS on one port
#
# Keys are injected at runtime as Hugging Face Space SECRETS
# (AISSTREAM_API_KEY, GROQ_API_KEY) — never baked into the image.
#
# The full multi-host deployment is docker-compose.yml; this is the all-in-one.
# ──────────────────────────────────────────────────────────────────────────

# ---- stage 1: build the Next.js static export ----------------------------
FROM node:20-slim AS webbuilder
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci || npm install
COPY web/ ./
# Non-empty sentinel → the app resolves same-origin URLs at runtime (one origin
# serves UI + REST + WS). Empty-string NEXT_PUBLIC_* vars are NOT inlined by Next.
ENV HF_EXPORT=1 \
    NEXT_PUBLIC_API_BASE=__SAME_ORIGIN__ \
    NEXT_PUBLIC_REPLAY_BASE=__SAME_ORIGIN__ \
    NEXT_PUBLIC_WS_URL=__SAME_ORIGIN__
RUN npm run build

# ---- stage 2: runtime with embedded Postgres + Redis ---------------------
# Pin to bookworm: the PGDG + TimescaleDB apt repos below target Debian 12
# (libicu72 / libldap-2.5 / libgdal32). Plain python:3.12-slim now resolves to
# trixie (Debian 13), whose newer libs make those PG packages uninstallable.
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DEBIAN_FRONTEND=noninteractive

# Base tools + PGDG (Postgres 16 + PostGIS) + TimescaleDB apt repos
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl gnupg lsb-release ca-certificates redis-server \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] http://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey \
        | gpg --dearmor -o /etc/apt/trusted.gpg.d/timescaledb.gpg \
    && echo "deb https://packagecloud.io/timescale/timescaledb/debian/ bookworm main" \
        > /etc/apt/sources.list.d/timescaledb.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-16 postgresql-16-postgis-3 \
        timescaledb-2-postgresql-16 timescaledb-2-loader-postgresql-16 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY packages/ ./packages/
COPY services/ ./services/
RUN pip install --upgrade pip \
    && pip install \
        -e packages/contracts -e packages/common -e packages/geo \
        -e services/ingestor -e services/api -e services/replay -e services/cognition

COPY --from=webbuilder /web/out ./web_static
COPY deploy/hf/start.sh ./start.sh
RUN chmod +x ./start.sh

# Live by default; keys arrive via HF secrets at runtime.
ENV AIS_SOURCE=live \
    REDIS_URL=redis://localhost:6379/0 \
    DATABASE_URL=postgresql://trident:trident@localhost:5432/trident \
    GROQ_MODEL=llama-3.3-70b-versatile \
    FUSION_ENABLED=false \
    TRIDENT_STATIC_DIR=/app/web_static \
    LOG_LEVEL=INFO \
    PORT=7860

EXPOSE 7860
CMD ["./start.sh"]
