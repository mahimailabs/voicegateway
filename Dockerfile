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
RUN pip install --no-cache-dir -e ".[cloud,dashboard,mcp]"

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
