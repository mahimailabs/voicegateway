# Docker Deployment

Deploy VoiceGateway in production with Docker Compose. This setup includes the API server, web dashboard, persistent storage, health checks, and an optional Ollama sidecar for local LLM inference.

## Project Structure

```
your-project/
  docker-compose.yml
  voicegw.yaml
  .env
```

## Environment Variables

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

::: warning
Never commit `.env` files to version control. Add `.env` to your `.gitignore`.
:::

### Generating a Fernet Key

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

models:
  stt:
    deepgram/nova-3:
      provider: deepgram
      model: nova-3
  llm:
    openai/gpt-4.1-mini:
      provider: openai
      model: gpt-4.1-mini
    anthropic/claude-sonnet-4-20250514:
      provider: anthropic
      model: claude-sonnet-4-20250514
  tts:
    cartesia/sonic-3:
      provider: cartesia
      model: sonic-3
      default_voice: 794f9389-aac1-45b6-b726-9d9369183238

stacks:
  premium:
    stt: deepgram/nova-3
    llm: openai/gpt-4.1-mini
    tts: cartesia/sonic-3

fallbacks:
  stt:
    - deepgram/nova-3
  llm:
    - openai/gpt-4.1-mini
    - anthropic/claude-sonnet-4-20250514
  tts:
    - cartesia/sonic-3
    - elevenlabs/turbo-v2.5

projects:
  prod:
    name: Production
    daily_budget: 100.00
    budget_action: throttle
    default_stack: premium
    tags: [production]

cost_tracking:
  enabled: true

rate_limits:
  openai:
    requests_per_minute: 60
  deepgram:
    requests_per_minute: 100
```

## Docker Compose

```yaml
version: "3.8"

services:
  voicegateway:
    build: .
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
      - VOICEGW_PROFILE=${VOICEGW_PROFILE:-production}
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

  dashboard:
    build:
      context: .
      dockerfile: dashboard/Dockerfile
    container_name: voicegateway-dash
    ports:
      - "9090:9090"
    volumes:
      - voicegw-data:/data:ro
    environment:
      - VOICEGW_API_URL=http://voicegateway:8080
      - VOICEGW_DB_PATH=/data/voicegw.db
    depends_on:
      voicegateway:
        condition: service_healthy
    restart: unless-stopped
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

## Starting the Services

### Cloud-only (API + Dashboard)

```bash
docker compose up -d
```

This starts:
- **voicegateway** on port 8080 (HTTP API)
- **dashboard** on port 9090 (Web UI)

### With Local Ollama

```bash
docker compose --profile local up -d

# Pull a model into Ollama
docker exec voicegateway-ollama ollama pull qwen2.5:3b
```

This adds:
- **ollama** on port 11434

Update `voicegw.yaml` to use the container hostname:

```yaml
providers:
  ollama:
    base_url: http://ollama:11434
```

## Verifying the Deployment

### Health Check

```bash
curl http://localhost:8080/health
```

```json
{
  "status": "ok",
  "uptime_seconds": 42.3,
  "version": "0.1.0"
}
```

### Provider Status

```bash
curl http://localhost:8080/v1/status
```

### Dashboard

Open http://localhost:9090 in your browser to see the dashboard with cost charts, latency metrics, and request logs.

## Production Considerations

### Persistent Storage

The `voicegw-data` volume stores the SQLite database. This persists across container restarts and rebuilds. To back up:

```bash
# Copy the database out of the volume
docker cp voicegateway:/data/voicegw.db ./backup-$(date +%Y%m%d).db
```

### Encryption Key Persistence

If you do not set `VOICEGW_SECRET`, a new Fernet key is generated on first run and stored in the container filesystem (not the volume). This means:

- Rebuilding the container loses the key
- Encrypted API keys in the database become undecryptable
- You will need to re-add managed providers

**Always set `VOICEGW_SECRET` in production** via the `.env` file or a secrets manager.

### Reverse Proxy

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
      - dashboard
    networks:
      - voicegw-net
```

### Resource Limits

For production deployments, add resource constraints:

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

VoiceGateway logs to stdout. Use Docker's logging driver to ship logs:

```yaml
  voicegateway:
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

## Connecting Your Application

From your voice agent application, point requests to the VoiceGateway API:

```python
from voicegateway import Gateway

# Point to the Docker service
gw = Gateway(config_path="/path/to/voicegw.yaml")

# Or use the HTTP API directly
import httpx
resp = httpx.get("http://localhost:8080/v1/status")
```

If your application runs in a separate container on the same Docker network, use the service name:

```python
# From another container on voicegw-net
resp = httpx.get("http://voicegateway:8080/v1/status")
```

## Updating

```bash
# Pull latest code and rebuild
git pull
docker compose build
docker compose up -d

# The SQLite database auto-migrates on startup
# No manual migration steps needed
```
