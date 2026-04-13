FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install voicegateway
COPY pyproject.toml README.md ./
COPY voicegateway/ ./voicegateway/
RUN pip install --no-cache-dir -e ".[cloud,dashboard]"

# Create data directory
RUN mkdir -p /data

# Health check endpoint
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

EXPOSE 8080

CMD ["voicegw", "serve", "--host", "0.0.0.0", "--port", "8080"]
