---
title: Docker Deployment
description: Production-ready Docker Compose configuration with health checks, persistent storage, and an optional Ollama sidecar.
---
Deploy VoiceGateway in production with Docker Compose. The
daemon serves the HTTP API and the web dashboard on the same port,
so one service is enough. Includes persistent storage, health checks,
and an optional Ollama sidecar for local LLM inference.

For hosting the collector on a VPS, Railway, or Fly.io, see [Deployment](/deployment/index).

## Project structure

```
your-project/
  docker-compose.yml
  voicegw.yaml
  .env
```

## Environment variables

Create a `.env` file with your provider API keys:

```bash
# .env
DEEPGRAM_API_KEY=your-deepgram-key
OPENAI_API_KEY=your-openai-key
ANTHROPIC_API_KEY=your-anthropic-key
GROQ_API_KEY=your-groq-key
CARTESIA_API_KEY=your-cartesia-key
ELEVENLABS_API_KEY=your-elevenlabs-key
ASSEMBLYAI_API_KEY=your-assemblyai-key

# Optional: set a fixed Fernet key for encryption across container restarts
VOICEGW_SECRET=your-base64-fernet-key
```

<Warning>
Never commit `.env` files to version control. Add `.env` to your `.gitignore`.
</Warning>

### Generating a Fernet key

If you do not set `VOICEGW_SECRET`, VoiceGateway auto-generates one on first run and stores it in the container. Since containers are ephemeral, set this explicitly for production:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Configuration

Create `voicegw.yaml`:

```yaml
providers:
  openai:
    api_key: ${OPENAI_API_KEY}
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}
  cartesia:
    api_key: ${CARTESIA_API_KEY}
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
  groq:
    api_key: ${GROQ_API_KEY}
  elevenlabs:
    api_key: ${ELEVENLABS_API_KEY}

projects:
  prod:
    name: Production
    daily_budget: 100.00
    budget_action: throttle
    tags: [production]

cost_tracking:
  enabled: true

rate_limits:
  openai:
    requests_per_minute: 60
  deepgram:
    requests_per_minute: 100
```

<Warning>
`budget_action` (here `throttle`) does not change gateway behavior by itself: it's a status label, not an enforcement switch. `attach()` only records spend against `daily_budget`. See [Projects](/configuration/projects#budgets) for what `warn`/`throttle`/`block` actually do, and use [`guard()`](/guide/guard) if you need a call actually stopped or rerouted once a cap is hit.
</Warning>

## Docker Compose

This uses the published image, so the project directory above (just `docker-compose.yml`, `voicegw.yaml`, `.env`) is all you need. Mount `voicegw.yaml` and point `VOICEGW_CONFIG` at it. The container boots without a config file (it warns once and falls back to built-in defaults), but a default-config daemon has no providers, models, or projects declared, which is not what you want in a deployment.

```yaml
services:
  voicegateway:
    image: mahimairaja/voicegateway:0.22.3
    container_name: voicegateway
    ports:
      - "8080:8080"
    volumes:
      - voicegw-data:/data
      - ./voicegw.yaml:/app/voicegw.yaml:ro
    environment:
      - VOICEGW_CONFIG=/app/voicegw.yaml
      - VOICEGW_DB_PATH=/data/voicegw.db
      - VOICEGW_SECRET=${VOICEGW_SECRET:-}
      # Provider API keys from .env
      - DEEPGRAM_API_KEY=${DEEPGRAM_API_KEY:-}
      - OPENAI_API_KEY=${OPENAI_API_KEY:-}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
      - GROQ_API_KEY=${GROQ_API_KEY:-}
      - CARTESIA_API_KEY=${CARTESIA_API_KEY:-}
      - ELEVENLABS_API_KEY=${ELEVENLABS_API_KEY:-}
      - ASSEMBLYAI_API_KEY=${ASSEMBLYAI_API_KEY:-}
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 5s
      retries: 3
    networks:
      - voicegw-net

  # Optional: local LLM via Ollama
  ollama:
    image: ollama/ollama:latest
    container_name: voicegateway-ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama-models:/root/.ollama
    profiles:
      - local  # Only starts with: docker compose --profile local up
    restart: unless-stopped
    networks:
      - voicegw-net

volumes:
  voicegw-data:
    driver: local
  ollama-models:
    driver: local

networks:
  voicegw-net:
    driver: bridge
```

### Building from source instead

The image above is the right choice for the 3-file project directory above. To build from source instead, you need the full repo, not just those 3 files: the Dockerfile's build context COPYs `pyproject.toml`, `src/`, `alembic.ini`, and `alembic/`, none of which exist in a standalone project directory.

```bash
git clone https://github.com/mahimailabs/voicegateway
cd voicegateway
```

Then put your `voicegw.yaml` and `.env` at the repo root, and replace the `image:` line in the Compose file above with:

```yaml
    build:
      context: .
      dockerfile: src/voicegateway/Dockerfile
```

## Starting the services

### Cloud-only (API + Dashboard)

```bash
docker compose up -d
```

This starts the **voicegateway** service on port 8080. The daemon serves both
the HTTP API and the React dashboard SPA on the same port.

### With local Ollama

```bash
docker compose --profile local up -d

# Pull a model into Ollama
docker exec voicegateway-ollama ollama pull qwen2.5:3b
```

Update `voicegw.yaml` to use the container hostname:

```yaml
providers:
  ollama:
    base_url: http://ollama:11434
```

### Fleet collector (Postgres)

To run the self-hosted collector that many LiveKit agents push telemetry to,
use the Postgres-backed stack. That compose file ships in the repository and is
not one of the three files above, so fetch it first:

```bash
curl -fsSLO https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docker-compose.collector.yml
docker compose -f docker-compose.collector.yml up -d
```

This starts a `postgres` service and a `collector` service. The collector reads
its database from `VOICEGW_DB_URL` and builds its schema on first start.
Set `VOICEGW_PG_PASSWORD` for anything beyond a local trial. Point the official
image at any Postgres directly without compose:

```bash
docker run -p 8080:8080 \
  -e VOICEGW_DB_URL="postgresql+asyncpg://user:pass@host:5432/voicegw" \
  -v $(pwd)/voicegw.yaml:/app/voicegw.yaml:ro \
  -e VOICEGW_CONFIG=/app/voicegw.yaml \
  mahimairaja/voicegateway:0.22.3
```

## Verifying the deployment

```bash
# Health check
curl http://localhost:8080/health
```

```json
{
  "status": "ok",
  "uptime_seconds": 42.3,
  "version": "0.22.3"
}
```

```bash
# Provider status
curl http://localhost:8080/v1/status

# Open the dashboard
open http://localhost:8080
```

## Production considerations

### Persistent storage

The `voicegw-data` volume stores the SQLite database. To back up:

```bash
docker cp voicegateway:/data/voicegw.db ./backup-$(date +%Y%m%d).db
```

### Encryption key persistence

**Always set `VOICEGW_SECRET` in production.** If you do not set it, a new Fernet key is generated on first run and stored in the container filesystem. Rebuilding the container loses the key, making encrypted API keys in the database unreadable.

### Reverse proxy

For TLS termination, put Nginx or Caddy in front:

```yaml
  nginx:
    image: nginx:alpine
    ports:
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./certs:/etc/nginx/certs:ro
    depends_on:
      - voicegateway
    networks:
      - voicegw-net
```

### Resource limits

```yaml
  voicegateway:
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 1G
        reservations:
          cpus: "0.5"
          memory: 256M
```

### Logging

```yaml
  voicegateway:
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

## Updating

Bump the image tag in `docker-compose.yml` (see [Auto-update the daemon](/deployment/auto-update) to automate this on a self-hosted box), then:

```bash
docker compose pull
docker compose up -d
# The SQLite database auto-migrates on startup.
```

If you're building from source instead, pull the repo and rebuild:

```bash
git pull
docker compose build
docker compose up -d
```
