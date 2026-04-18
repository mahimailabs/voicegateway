# Changelog

All notable changes to VoiceGateway are documented here. This project follows [Semantic Versioning](https://semver.org/) and [Conventional Commits](https://www.conventionalcommits.org/).

## v0.1.0 -- 2026-04-17

**Initial release** of VoiceGateway -- a self-hosted inference gateway for voice AI.

### Core

- `Gateway` class with `stt()`, `llm()`, `tts()` methods for unified request routing
- YAML configuration (`voicegw.yaml`) with `${ENV_VAR}` substitution
- `Router` for resolving `provider/model` strings to provider instances
- `Registry` with lazy provider imports -- only loads SDKs when configured
- `ModelId` parser for `provider/model` format strings
- Config search order: `./voicegw.yaml`, `~/.config/voicegateway/voicegw.yaml`, `/etc/voicegateway/voicegw.yaml`

### Providers (11)

**Cloud providers:**
- OpenAI -- STT (Whisper), LLM (GPT-4o, GPT-4o-mini, GPT-4.1-mini), TTS
- Deepgram -- STT (Nova-2, Nova-3, Flux), TTS (Aura-2)
- Anthropic -- LLM (Claude 3.5 Sonnet)
- Groq -- STT (Whisper Large V3), LLM (Llama 3.1 70B, Llama 3.1 8B)
- Cartesia -- TTS (Sonic-3)
- ElevenLabs -- TTS (Eleven Turbo V2.5)
- AssemblyAI -- STT (Universal-2)

**Local models:**
- Whisper -- STT via `faster-whisper` (Large V3, Turbo, Base)
- Kokoro -- TTS via `kokoro-onnx`
- Piper -- TTS via `piper-tts`
- Ollama -- LLM (any Ollama-hosted model)

### Middleware

- **Cost tracker** -- per-request cost calculation using built-in pricing catalog
- **Budget enforcer** -- per-project daily budgets with `warn` or `block` actions
- **Fallback chains** -- per-modality automatic failover when providers are down
- **Rate limiter** -- configurable per-provider request rate limits
- **Latency monitor** -- TTFB and total latency tracking per request
- **Request logger** -- full request metadata stored for audit

### Storage

- SQLite backend via `aiosqlite`
- `RequestRecord` dataclass for structured request metadata
- SQL views for daily cost aggregation and per-project summaries
- Default database path: `~/.config/voicegateway/voicegw.db`

### HTTP API

- FastAPI server at configurable port (default: 8080)
- Endpoints: `/health`, `/v1/status`, `/v1/models`, `/v1/costs`, `/v1/projects`, `/v1/logs`, `/v1/metrics`
- CORS enabled for dashboard access

### Dashboard

- React/TypeScript/Vite frontend with Neo-Brutalism design
- Cost breakdown charts by project, provider, and modality (Recharts)
- Latency percentile graphs
- Request log browser
- FastAPI backend serving dashboard data from SQLite

### MCP Server

- 17 tools for managing the gateway from coding agents
- Transports: stdio (local) and HTTP/SSE (remote)
- Authentication via `VOICEGW_MCP_TOKEN` (HTTP/SSE only)
- Constant-time token comparison (`hmac.compare_digest`)
- Compatible with Claude Code, Cursor, Codex, Cline

### CLI

- `voicegw init` -- generate a starter `voicegw.yaml`
- `voicegw serve --port 8080` -- start the HTTP API server
- `voicegw dashboard` -- start the web dashboard (port 9090)
- `voicegw status` -- show provider health and configuration
- `voicegw mcp` -- start the MCP server

### Packaging

- Modular extras: `pip install voicegateway[openai,deepgram]`
- Aggregate extras: `cloud`, `local`, `all`, `dashboard`, `mcp`, `dev`
- Docker Compose with optional Ollama profile
- MIT license

### Testing

- 200+ tests with pytest
- `asyncio_mode = "auto"` -- no manual async markers needed
- Shared fixtures: `_test_env`, `example_config_path`, `temp_config`, `seeded_storage`
- Coverage target: >70%

---

*Future releases will be appended here.*

## Related pages

- [Version Upgrades](/migration/version-upgrades)
- [FAQ](/reference/faq)
- [Contributing](/contributing/)
