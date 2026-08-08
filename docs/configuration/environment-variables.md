---
title: Environment variables
description: Every environment variable VoiceGateway reads (config path, database, daemon bind, Fernet keys, MCP token, cloud ingest) and how ${VAR_NAME} substitution works in voicegw.yaml.
---
VoiceGateway reads environment variables for configuration overrides, secret material, and daemon binding. Variables can also be referenced in `voicegw.yaml` using `${VAR_NAME}` syntax.

## VoiceGateway variables

| Variable | Purpose | Example |
|---|---|---|
| `VOICEGW_CONFIG` | Override the config file path. Skips the default discovery order. | `/opt/voicegw/config.yaml` |
| `VOICEGW_DB_PATH` | Override the SQLite database path. Also enables cost tracking when set. | `~/.config/voicegateway/voicegw.db` |
| `VOICEGW_HOST` | Bind host for `python -m voicegateway.server.main` (the Docker entrypoint). The CLI uses `serve.host` from the config; this var is for module invocations. | `127.0.0.1` |
| `VOICEGW_PORT` | Bind port for `python -m voicegateway.server.main`. Same scope as `VOICEGW_HOST`. | `8080` |
| `VOICEGW_SECRET` | Fernet key for encrypting managed-provider API keys before they land in SQLite. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. | (44-char base64 string) |
| `VOICEGW_SECRET_FALLBACK` | Comma-separated previous Fernet keys for rotation. Lets `voicegw rotate-secret` re-encrypt rows stored under an older key. | (44-char base64 string) |
| `VOICEGW_MCP_TOKEN` | Bearer token for authenticating MCP server requests when running over HTTP/SSE. | `mcp-secret-token` |
| `VOICEGW_ACTIVE_PROJECT` | Active project for the deprecated `voicegateway.LLM/STT/TTS` factories only (resolution: ContextVar, then this env var, then `default_project`). `attach()` takes its project from the `project=` argument, not this variable. | `customer-support` |

## Cloud ingest variables

These variables configure the agent-side remote sink that pushes telemetry to VoiceGateway Cloud (or a self-hosted collector):

| Variable | Purpose |
|---|---|
| `VOICEGW_COLLECTOR_URL` | Base URL of the collector, e.g. `https://collect.voicegateway.dev`. The engine appends `/v1/ingest` (a full `.../v1/ingest` URL is also accepted). |
| `VOICEGW_API_KEY` | Two roles, depending on which side sets it. **On an agent**, the key it presents to the collector, normally a virtual key (`vk_...`). **On a collector**, a static wildcard ingest key it will accept. A collector value starting with `vk_` routes to virtual-key lookup in the database instead, so every request fails with `{"detail":"Invalid virtual key"}`: generate a collector key with `openssl rand -hex 32`. |
| `VOICEGW_PROJECT` | Default project name for `attach()` calls. Resolution order: `project=` argument, then this env var, then `"default"`. |

Set all three in the agent's environment. `attach()` reads them automatically. The `project=` argument in your `attach(target, project="...")` call always takes precedence over `VOICEGW_PROJECT`. See [Hosted quickstart](/hosted/quickstart) for the full setup flow.

## Provider API keys

Each cloud provider reads its API key from a standard environment variable. Reference these in `voicegw.yaml` via `${VAR_NAME}` substitution.

| Variable | Provider | Required for |
|---|---|---|
| `DEEPGRAM_API_KEY` | Deepgram | STT, TTS |
| `OPENAI_API_KEY` | OpenAI | STT, LLM, TTS |
| `ANTHROPIC_API_KEY` | Anthropic | LLM |
| `GROQ_API_KEY` | Groq | STT, LLM |
| `CARTESIA_API_KEY` | Cartesia | TTS |
| `ELEVENLABS_API_KEY` | ElevenLabs | TTS |
| `ASSEMBLYAI_API_KEY` | AssemblyAI | STT |

## How substitution works

Any string value in `voicegw.yaml` can reference an environment variable with `${VAR_NAME}`:

```yaml
providers:
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}
  openai:
    api_key: ${OPENAI_API_KEY}
    base_url: ${OPENAI_BASE_URL}
```

VoiceGateway substitutes these at config load time from `os.environ`. Substitution works recursively through all dicts and lists. If a variable is not set, it resolves to an empty string.

## Setting environment variables

### Shell export

```bash
export DEEPGRAM_API_KEY="your-key-here"
export OPENAI_API_KEY="your-key-here"
export VOICEGW_DB_PATH="~/.config/voicegateway/voicegw.db"
```

### `.env` file

VoiceGateway does not load `.env` files automatically. Use `direnv` or a similar tool if you prefer file-based management:

```bash
echo 'export DEEPGRAM_API_KEY="your-key"' >> .envrc
direnv allow
```

### Docker

```bash
docker compose up -d \
  -e DEEPGRAM_API_KEY=your-key \
  -e OPENAI_API_KEY=your-key \
  -e VOICEGW_SECRET=your-secret
```

Or in `docker-compose.yml`:

```yaml
services:
  voicegw:
    environment:
      - DEEPGRAM_API_KEY=${DEEPGRAM_API_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - VOICEGW_SECRET=${VOICEGW_SECRET}
```

## Config discovery order

When `VOICEGW_CONFIG` is not set, VoiceGateway searches for config in this order:

1. `./voicegw.yaml`
2. `~/.config/voicegateway/voicegw.yaml`
3. `/etc/voicegateway/voicegw.yaml`

`voicegw init` writes to the second path by default.

---

See [voicegw.yaml reference](/configuration/voicegw-yaml) for all config keys.
See [Providers](/configuration/providers) for per-provider credential blocks.
