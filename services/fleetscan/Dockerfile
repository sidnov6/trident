FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY packages/ ./packages/
COPY services/fleetscan/ ./services/fleetscan/
RUN pip install --upgrade pip \
    && pip install \
        -e packages/contracts -e packages/common -e packages/geo \
        -e services/fleetscan
CMD ["python", "-m", "fleetscan.main"]
