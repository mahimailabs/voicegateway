# mahimairaja/voicegateway

Self-hosted inference gateway for voice AI. Unified STT + LLM + TTS routing with MCP server for coding agents.

Full documentation: **[docs.voicegateway.dev](https://docs.voicegateway.dev)**

---

## Quick Start

```bash
# 1. Create a config file
mkdir -p voicegw-data
curl -sL https://raw.githubusercontent.com/mahimailabs/voicegateway/main/voicegw.example.yaml \
  -o voicegw-data/voicegw.yaml
# Edit voicegw.yaml with your API keys

# 2. Run the container
docker run -d \
  --name voicegateway \
  -p 8080:8080 \
  -v $(pwd)/voicegw-data:/data \
  mahimairaja/voicegateway:latest
```

Visit `http://localhost:8080/health` to verify it's running.

---

## Docker Compose (with dashboard)

```yaml
services:
  voicegateway:
    image: mahimairaja/voicegateway:latest
    ports: ["8080:8080"]
    volumes:
      - ./voicegw-data:/data
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - DEEPGRAM_API_KEY=${DEEPGRAM_API_KEY}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 5s
      retries: 3

  dashboard:
    image: mahimairaja/voicegateway-dashboard:latest
    ports: ["9090:9090"]
    depends_on:
      - voicegateway
```

```bash
docker compose up -d
```

Dashboard at `http://localhost:9090`.

---

## MCP Server (agent-native management)

The combined server includes MCP SSE at `/mcp/sse`. Set a bearer token:

```bash
docker run -d \
  --name voicegateway \
  -p 8080:8080 \
  -v $(pwd)/voicegw-data:/data \
  -e VOICEGW_MCP_TOKEN=$(openssl rand -hex 32) \
  mahimairaja/voicegateway:latest
```

Then add to Claude Code:

```bash
claude mcp add voicegateway \
  --transport sse \
  --url http://localhost:8080/mcp/sse \
  --header "Authorization: Bearer $VOICEGW_MCP_TOKEN"
```

---

## Tags

- `latest` -- most recent release
- `{version}` -- e.g., `0.1.0`, `0.2.0`
- `{major}.{minor}` -- e.g., `0.1` -- pins to minor version

## Architectures

- `linux/amd64` -- Intel/AMD servers, Docker Desktop
- `linux/arm64` -- Apple Silicon Macs, ARM servers (Hetzner, Oracle, AWS Graviton)

## Volumes

- `/data` -- persistent storage for SQLite database, encryption secret, config file

## Environment Variables

| Variable | Purpose |
|---|---|
| `VOICEGW_CONFIG` | Path to voicegw.yaml (default: /data/voicegw.yaml) |
| `VOICEGW_DB_PATH` | SQLite path (default: /data/voicegw.db) |
| `VOICEGW_SECRET` | Fernet encryption key (default: auto-generated) |
| `VOICEGW_MCP_TOKEN` | Bearer token for MCP HTTP/SSE |
| `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, etc. | Provider API keys |

## Ports

- `8080` -- HTTP API + MCP SSE endpoint + Dashboard

## Source

[github.com/mahimailabs/voicegateway](https://github.com/mahimailabs/voicegateway) -- MIT License
