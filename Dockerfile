# Dockerfile (core gateway)
# Multi-stage build: builder installs deps, runtime has only what's needed.

# -------- Stage 1: Frontend builder --------
FROM node:20-alpine AS frontend-builder

WORKDIR /build/frontend
COPY dashboard/frontend/package*.json ./
RUN npm ci
COPY dashboard/frontend/ ./
RUN npm run build

# -------- Stage 2: Python builder --------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY voicegateway/ ./voicegateway/

ARG VERSION=0.1.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION}

RUN pip install --prefix=/install ".[cloud,mcp]"

# -------- Stage 3: Runtime --------
FROM python:3.12-slim AS runtime

ARG VERSION=0.1.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VOICEGW_CONFIG=/data/voicegw.yaml \
    VOICEGW_DB_PATH=/data/voicegw.db \
    PATH="/install/bin:${PATH}" \
    PYTHONPATH="/install/lib/python3.12/site-packages"

LABEL org.opencontainers.image.title="VoiceGateway" \
      org.opencontainers.image.description="Self-hosted inference gateway for voice AI with MCP" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.url="https://voicegateway.dev" \
      org.opencontainers.image.documentation="https://docs.voicegateway.dev" \
      org.opencontainers.image.source="https://github.com/mahimailabs/voicegateway" \
      org.opencontainers.image.authors="Mahimai Labs" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.vendor="Mahimai Labs"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r voicegw --gid=1000 \
    && useradd -r -g voicegw --uid=1000 --home-dir=/data --shell=/bin/bash voicegw \
    && mkdir -p /data \
    && chown -R voicegw:voicegw /data

COPY --from=builder /install /install
COPY --chown=voicegw:voicegw voicegateway/ /app/voicegateway/
COPY --chown=voicegw:voicegw dashboard/api/ /app/dashboard/api/
COPY --chown=voicegw:voicegw dashboard/__init__.py /app/dashboard/
COPY --from=frontend-builder --chown=voicegw:voicegw /build/frontend/dist /app/dashboard/frontend/dist
COPY --chown=voicegw:voicegw voicegw.example.yaml /app/

USER voicegw
WORKDIR /app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["python", "-m", "voicegateway.combined_server"]
