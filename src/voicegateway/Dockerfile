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

# -------- Stage 1b: Console builder --------
# The OpenOrca fleet console (/console) is a separate React 19 + Tailwind v4 +
# Vite 7 SPA, so it builds in its own stage on Node 22 (Vite 7 needs >=20.19).
FROM node:22-alpine AS console-builder

WORKDIR /build/console
COPY src/dashboard/console/package*.json ./
RUN npm ci
COPY src/dashboard/console/ ./
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
# The wheel force-includes the migrations (pyproject
# [tool.hatch.build.targets.wheel.force-include]), so hatchling needs
# alembic.ini + alembic/ present at build time, not just copied into the
# runtime stage below. Without this the build fails with
# "Forced include not found: /build/alembic".
COPY alembic.ini ./
COPY alembic/ ./alembic/

# Deliberately not a plausible release number. .dockerignore excludes .git,
# so hatch-vcs cannot derive a version inside the build and this ARG is the
# only source. The release workflow passes --build-arg VERSION=<tag>; any
# build that does not (a Railway source deploy, a local docker build) falls
# back to this. The old default was 0.5.0, which reads as a real release and
# sends anyone checking /health to the wrong changelog. An obvious placeholder
# makes an unstamped build self-identifying instead.
ARG VERSION=0.0.0.dev0+unstamped
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION}

# The `collector` extra bundles the dashboard/server stack (web UI + TUI + MCP
# server) plus asyncpg (so the image can run as a Postgres fleet collector via
# VOICEGW_DB_URL, not just embedded SQLite) and duckdb. No provider wheels: the
# collector meters by model_id via voice-prices, it does not build providers.
RUN pip install --prefix=/install ".[collector]"

# -------- Stage 3: Runtime --------
FROM python:3.12-slim AS runtime

ARG VERSION=0.0.0.dev0+unstamped

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
# The source tree copied above lacks the hatch-vcs _version.py: it is gitignored
# and written only at build time. /app is the WORKDIR and shadows the installed
# package on sys.path, so without _version.py here the very first import
# (``from voicegateway._version import __version__`` in __init__.py) crashes the
# server on startup. Restore it from the wheel the builder already installed.
COPY --from=builder --chown=voicegw:voicegw \
    /install/lib/python3.12/site-packages/voicegateway/_version.py \
    /app/voicegateway/_version.py
COPY --chown=voicegw:voicegw src/dashboard/__init__.py /app/dashboard/
COPY --chown=voicegw:voicegw src/dashboard/api/static/ /app/dashboard/api/static/
COPY --from=frontend-builder --chown=voicegw:voicegw /build/frontend/dist /app/dashboard/frontend/dist
# The console mounts at /console; static.mount_console resolves its dev-layout
# candidate (dashboard/console/dist) relative to /app, matching the dashboard above.
COPY --from=console-builder --chown=voicegw:voicegw /build/console/dist /app/dashboard/console/dist

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

# Resolves the port EXACTLY as server.main does: VOICEGW_PORT, else the
# platform's PORT, else 8080. Hardcoding 8080 here made the container
# permanently unhealthy the moment a platform injected PORT, because the app
# then listened somewhere this probe never looked. That is not cosmetic:
# docker-compose.yml gates the dashboard on `condition: service_healthy`, so an
# unhealthy daemon means the dashboard never starts at all.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS "http://localhost:${VOICEGW_PORT:-${PORT:-8080}}/health" || exit 1

CMD ["python", "-m", "voicegateway.server.main"]
