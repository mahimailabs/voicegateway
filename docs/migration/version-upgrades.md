# Version Upgrades

This page documents breaking changes, migration steps, and upgrade notes for each VoiceGateway release.

## Upgrade process

For any version upgrade:

```bash
# 1. Check your current version
voicegw --version

# 2. Upgrade
pip install --upgrade voicegateway

# 3. Check for config schema changes
voicegw init --diff

# 4. Run your tests
pytest

# 5. Restart the server
voicegw serve --port 8080
```

If you use Docker:

```bash
docker compose pull
docker compose up -d
```

## Version history

### v0.1.0 -- Initial release

**Release date:** 2026-04-17

This is the first public release of VoiceGateway. There are no breaking changes to migrate from.

**Features:**

- **Gateway core** -- unified routing for STT, LLM, and TTS requests through `Gateway.stt()`, `Gateway.llm()`, `Gateway.tts()`
- **11 providers** -- OpenAI, Deepgram, Anthropic, Groq, Cartesia, ElevenLabs, AssemblyAI (cloud); Whisper, Kokoro, Piper, Ollama (local)
- **Configuration** -- YAML config at `voicegw.yaml` with `${ENV_VAR}` substitution
- **Cost tracking** -- per-request cost calculation using built-in pricing catalog, stored in SQLite
- **Budget enforcement** -- per-project daily budgets with `warn` or `block` actions
- **Fallback chains** -- per-modality fallback when primary provider fails
- **Rate limiting** -- configurable per-provider rate limits
- **Latency monitoring** -- TTFB and total latency tracking per request
- **Request logging** -- full request metadata stored for audit and debugging
- **Web dashboard** -- React/TypeScript frontend with cost charts, latency graphs, request logs
- **HTTP API** -- FastAPI server with `/health`, `/v1/status`, `/v1/models`, `/v1/costs`, `/v1/projects`, `/v1/logs`, `/v1/metrics`
- **MCP server** -- 17 tools for managing the gateway from Claude Code, Cursor, Codex, and other coding agents
- **CLI** -- `voicegw init`, `voicegw serve`, `voicegw dashboard`, `voicegw status`, `voicegw mcp`
- **Docker support** -- `docker-compose.yml` with optional Ollama profile
- **Modular installs** -- `pip install voicegateway[openai,deepgram]` installs only the SDKs you need

**Config format (v0.1.0):**

```yaml
providers:
  <name>:
    api_key: ${ENV_VAR}
    # provider-specific options

models:
  stt:
    <provider/model>:
      provider: <name>
      model: <model>
  llm: { ... }
  tts: { ... }

stacks:
  <name>:
    stt: <provider/model>
    llm: <provider/model>
    tts: <provider/model>

projects:
  <slug>:
    name: <display name>
    daily_budget: <float>
    budget_action: warn | block

fallbacks:
  stt: [...]
  llm: [...]
  tts: [...]

cost_tracking:
  enabled: true
  db_path: <path>  # optional, defaults to ~/.config/voicegateway/voicegw.db
```

---

*Future releases will be documented here with breaking changes, deprecations, and migration steps.*

## Versioning policy

VoiceGateway follows [Semantic Versioning](https://semver.org/):

- **Patch** (0.1.x): bug fixes, no config changes
- **Minor** (0.x.0): new features, backward-compatible config changes
- **Major** (x.0.0): breaking changes to config format, Python API, or HTTP API

Before any breaking change, VoiceGateway will:

1. Deprecate the old behavior with a warning for at least one minor release
2. Document the migration path on this page
3. Provide `voicegw init --diff` output showing required config changes

## Related pages

- [Changelog](/reference/changelog)
- [Installation](/guide/installation)
- [Migrating from LiteLLM](/migration/from-litellm)
- [Migrating from LiveKit Inference](/migration/from-livekit-inference)
