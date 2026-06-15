# TRIDENT api image (Tier 4 — the FastAPI gateway).
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
COPY services/api/ /app/services/api/

RUN pip install --upgrade pip && \
    pip install \
        -e packages/contracts \
        -e packages/common \
        -e packages/geo \
        -e services/api

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
