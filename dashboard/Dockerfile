# dashboard/Dockerfile
# Multi-stage build: Node builds frontend, Python serves the dashboard API.

# -------- Stage 1: Frontend builder --------
FROM node:20-alpine AS frontend-builder

WORKDIR /build/frontend
COPY dashboard/frontend/package*.json ./
RUN npm ci
COPY dashboard/frontend/ ./
RUN npm run build

# -------- Stage 2: Python runtime --------
FROM python:3.12-slim AS runtime

ARG VERSION=0.1.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.title="VoiceGateway Dashboard" \
      org.opencontainers.image.description="Web dashboard for VoiceGateway with full CRUD" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/mahimailabs/voicegateway" \
      org.opencontainers.image.authors="Mahimai Labs" \
      org.opencontainers.image.licenses="MIT"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r dashboard --gid=1000 \
    && useradd -r -g dashboard --uid=1000 --home-dir=/app --shell=/bin/bash dashboard

WORKDIR /app

COPY pyproject.toml README.md ./
COPY voicegateway/ ./voicegateway/
COPY dashboard/api/ ./dashboard/api/
COPY dashboard/__init__.py ./dashboard/

ARG SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION}
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${SETUPTOOLS_SCM_PRETEND_VERSION}
RUN pip install --no-cache-dir -e ".[dashboard]"

COPY --from=frontend-builder /build/frontend/dist ./dashboard/frontend/dist

USER dashboard

EXPOSE 9090

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:9090/api/overview || exit 1

CMD ["uvicorn", "dashboard.api.main:app", "--host", "0.0.0.0", "--port", "9090"]
