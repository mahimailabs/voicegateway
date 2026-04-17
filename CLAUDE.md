# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

VoiceGateway — a self-hosted inference gateway for voice AI. Provides unified routing for STT, LLM, and TTS requests across cloud providers (OpenAI, Deepgram, Anthropic, Groq, Cartesia, ElevenLabs, AssemblyAI) and local models (Whisper, Kokoro, Piper). Includes cost tracking, fallback chains, rate limiting, and a web dashboard.

## Commands

```bash
# Install (editable, with dev dependencies)
pip install -e ".[dev]"

# Run tests
pytest
pytest tests/test_config.py              # single file
pytest tests/test_config.py::test_name   # single test
pytest --cov                             # with coverage

# CLI
voicegw init                             # create config template
voicegw serve --port 8080                # start HTTP API
voicegw dashboard                        # start web UI (port 9090)
voicegw status                           # show provider status

# Dashboard frontend (dashboard/frontend/)
npm install && npm run dev               # dev server
npm run build                            # production build

# Docker
docker compose up -d                     # API + Dashboard
docker compose --profile local up -d     # + Ollama
```

## Architecture

**Request flow:** User code → `Gateway.stt()`/`llm()`/`tts()` → Router → Provider → Middleware pipeline (cost tracking, latency, rate limiting, fallback) → SQLite storage → Dashboard reads stored data.

**Core (`voicegateway/core/`):**
- `gateway.py` — Main orchestrator, entry point for all requests
- `config.py` — YAML parser with `${ENV_VAR}` substitution
- `router.py` — Resolves `provider/model` strings to provider instances
- `registry.py` — Lazy provider factory (instantiates on first use)
- `model_id.py` — Parses `provider/model` format strings

**Providers (`voicegateway/providers/`):** Each extends `BaseProvider` from `base.py`. 11 implementations covering cloud and local models.

**Middleware (`voicegateway/middleware/`):** Cost tracking, latency monitoring, rate limiting, request logging, fallback chains. All wrap provider calls.

**Storage (`voicegateway/storage/`):** SQLite backend with `RequestRecord` dataclass. Includes SQL views for daily costs and per-project aggregation.

**HTTP API (`voicegateway/server.py`):** FastAPI with endpoints at `/health`, `/v1/status`, `/v1/models`, `/v1/costs`, `/v1/projects`, `/v1/logs`, `/v1/metrics`.

**Dashboard (`dashboard/`):** FastAPI backend (`api/`) + React/TypeScript/Vite frontend (`frontend/`). Uses Recharts for visualization. Neo-Brutalism design aesthetic.

**Public API:** `voicegateway/__init__.py` exports `Gateway`, `ModelId`, `GatewayConfig`.

## Key Patterns

- **Async throughout** — all DB, HTTP, and provider operations use async/await
- **Modular provider installs** — `pip install -e ".[openai,deepgram]"` installs only needed providers
- **Config format** — YAML at `voicegw.yaml`, env vars via `${VAR_NAME}` syntax
- **pytest-asyncio** — `asyncio_mode = "auto"` in pyproject.toml, no manual `@pytest.mark.asyncio` needed
- **Test fixtures** in `tests/conftest.py` set fake API keys for all providers
