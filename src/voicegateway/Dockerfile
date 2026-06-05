# src/voicegateway/Dockerfile (core gateway)
# Build context: repo root. Reference from docker-compose.yml as
# `dockerfile: src/voicegateway/Dockerfile`.
# Multi-stage build: builder installs deps, runtime has only what's needed.

# -------- Stage 1: Frontend builder --------
FROM node:20-alpine AS frontend-builder

WORKDIR /build/frontend
COPY src/dashboard/frontend/package*.json ./
RUN npm ci
COPY src/dashboard/frontend/ ./
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
# src/ layout: pyproject.toml's [tool.hatch.build.targets.wheel].packages
# points at src/voicegateway and src/dashboard, so hatchling needs both
# directories present at the path it expects.
COPY src/voicegateway/ ./src/voicegateway/
COPY src/dashboard/ ./src/dashboard/

ARG VERSION=0.5.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION}

# Include the `postgres` extra (asyncpg) so the image can run as a Postgres
# fleet collector via VOICEGW_DB_URL, not just embedded SQLite.
RUN pip install --prefix=/install ".[cloud,mcp,dashboard,tui,postgres]"

# -------- Stage 3: Runtime --------
FROM python:3.12-slim AS runtime

ARG VERSION=0.5.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VOICEGW_CONFIG=/data/voicegw.yaml \
    VOICEGW_DB_PATH=/data/voicegw.db \
    PATH="/install/bin:${PATH}" \
    PYTHONPATH="/install/lib/python3.12/site-packages"

LABEL org.opencontainers.image.title="VoiceGateway" \
      org.opencontainers.image.description="Cost tracking and reconciliation for LiveKit voice agents, with MCP server for coding agents" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.url="https://voicegateway.dev" \
      org.opencontainers.image.documentation="https://docs.voicegateway.dev" \
      org.opencontainers.image.source="https://github.com/mahimailabs/voicegateway" \
      org.opencontainers.image.authors="Mahimai Raja J" \
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
COPY --chown=voicegw:voicegw src/voicegateway/ /app/voicegateway/
COPY --chown=voicegw:voicegw src/dashboard/__init__.py /app/dashboard/
COPY --chown=voicegw:voicegw src/dashboard/api/static/ /app/dashboard/api/static/
COPY --from=frontend-builder --chown=voicegw:voicegw /build/frontend/dist /app/dashboard/frontend/dist

# Ship the migrations. On first DB access the runtime runs `alembic upgrade
# head`; Database._find_alembic_ini walks up from /app/voicegateway to /app, and
# alembic.ini's script_location is %(here)s/alembic, so both the ini and the
# versions tree must live at /app. Without this the server cannot build its
# schema (SQLite or a Postgres collector backend).
COPY --chown=voicegw:voicegw alembic.ini /app/alembic.ini
COPY --chown=voicegw:voicegw alembic/ /app/alembic/

USER voicegw
WORKDIR /app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["python", "-m", "voicegateway.server.main"]
