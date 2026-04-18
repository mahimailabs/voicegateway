# Stage 1: Build dashboard frontend
FROM node:20-slim AS frontend-builder

WORKDIR /build
COPY dashboard/frontend/package*.json ./
RUN npm ci
COPY dashboard/frontend/ ./
RUN npm run build

# Stage 2: Python runtime
FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install voicegateway with cloud providers, dashboard, and MCP
COPY pyproject.toml README.md ./
COPY voicegateway/ ./voicegateway/
COPY dashboard/ ./dashboard/
ARG SETUPTOOLS_SCM_PRETEND_VERSION=0.1.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${SETUPTOOLS_SCM_PRETEND_VERSION}
RUN pip install --no-cache-dir -e ".[cloud,dashboard,mcp]"

# Copy built frontend assets from stage 1
COPY --from=frontend-builder /build/dist ./dashboard/frontend/dist

# Create data directory (Fly volume mounts here)
RUN mkdir -p /data

# Create non-root user
RUN useradd --create-home --shell /bin/bash voicegw \
    && chown -R voicegw:voicegw /app /data

USER voicegw

# Health check endpoint
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

EXPOSE 8080

# Combined server: API + Dashboard + MCP SSE on single port
CMD ["python", "-m", "voicegateway.combined_server"]
