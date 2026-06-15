# TRIDENT cognition image (Tier 3 — the LangGraph agent swarm).
# Build context is the REPO ROOT (see docker-compose.yml: context = ".") so we
# can COPY the shared spine packages in alongside the service and install them
# all editable.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Spine packages first (rarely change -> better layer caching), then service.
COPY packages/ /app/packages/
COPY services/cognition/ /app/services/cognition/

RUN pip install --upgrade pip && \
    pip install \
        -e packages/contracts \
        -e packages/common \
        -e packages/geo \
        -e services/cognition

CMD ["python", "-m", "cognition.main"]
