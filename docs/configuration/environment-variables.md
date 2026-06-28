---
title: Environment variables
description: Every environment variable VoiceGateway reads (config path, database path, daemon bind, Fernet keys, MCP token) and how ${VAR_NAME} substitution works in voicegw.yaml.
---

# Environment variables

VoiceGateway reads environment variables for configuration
overrides, daemon binding, secret material, and provider API keys.
Variables can also be referenced in `voicegw.yaml` using
`${VAR_NAME}` syntax.

## VoiceGateway variables

| Variable | Purpose | Example |
|---|---|---|
| `VOICEGW_CONFIG` | Override the config file path. Skips the default search order. | `/opt/voicegw/config.yaml` |
| `VOICEGW_DB_PATH` | Override the SQLite database path. Also enables cost tracking when set. | `~/.config/voicegateway/voicegw.db` |
| `VOICEGW_HOST` | Bind host for `python -m voicegateway.server.main` (the Docker entrypoint). The CLI uses `serve.host` from the config; this var is for module invocations. | `127.0.0.1` |
| `VOICEGW_PORT` | Bind port for `python -m voicegateway.server.main`. Same scope as `VOICEGW_HOST`. | `8080` |
| `VOICEGW_SECRET` | Fernet key for encrypting managed-provider API keys before they land in SQLite. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. | (44-char base64 string) |
| `VOICEGW_SECRET_FALLBACK` | Comma-separated previous Fernet keys for rotation. Lets `voicegw rotate-secret` re-encrypt rows that were stored under an older key. | (44-char base64 string) |
| `VOICEGW_MCP_TOKEN` | Bearer token for authenticating MCP server requests when running over HTTP/SSE. | `mcp-secret-token` |

## Provider API keys

Each cloud provider reads its API key from a standard environment
variable. These are referenced in `voicegw.yaml` via `${VAR_NAME}`
substitution.

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

In `voicegw.yaml`, any string value can reference an environment
variable using `${VAR_NAME}`:

```yaml
providers:
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}
  openai:
    api_key: ${OPENAI_API_KEY}
    base_url: ${OPENAI_BASE_URL}
```

VoiceGateway substitutes these at config load time. If the
environment variable is not set, it resolves to an empty string.
Substitution works recursively through all dicts and lists in the
config.

## Setting environment variables

### Shell export

```bash
export DEEPGRAM_API_KEY="your-key-here"
export OPENAI_API_KEY="your-key-here"
export VOICEGW_DB_PATH="~/.config/voicegateway/voicegw.db"
```

### `.env` file

VoiceGateway does not load `.env` files automatically. Use a tool
like `direnv` or `dotenv` if you prefer file-based env var
management:

```bash
# With direnv
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

## Config search order

When `VOICEGW_CONFIG` is not set, VoiceGateway searches for config
in this order:

1. `./voicegw.yaml`
2. `~/.config/voicegateway/voicegw.yaml`
3. `/etc/voicegateway/voicegw.yaml`

`voicegw onboard` writes to the second path by default.

See [voicegw.yaml reference](/configuration/voicegw-yaml),
[Providers](/configuration/providers),
[Installation](/guide/installation).
