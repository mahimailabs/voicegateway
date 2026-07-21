# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project

VoiceGateway: cost tracking and reconciliation for LiveKit voice agents. Returns native LiveKit STT, LLM, and TTS plugin instances across cloud providers (OpenAI, Deepgram, Anthropic, Groq, Cartesia, ElevenLabs, AssemblyAI) and local models (Whisper, Kokoro, Piper). LLM, STT, and TTS prices all flow through `voice-prices` (a fork of `pydantic/genai-prices`). Ships `voicegw reconcile` for verifying recorded numbers against provider invoices, plus per-modality cost tracking, resolver-time fallback chains, rate limiting, and a web dashboard.

## Commands

```bash
# Install (editable, with dev dependencies)
pip install -e ".[dev]"

# Run tests
pytest
pytest src/voicegateway/tests/core/test_config.py              # single file
pytest src/voicegateway/tests/core/test_config.py::test_name   # single test
pytest --cov                                                   # with coverage

# CLI
voicegw init                             # create config template
voicegw serve --port 8080                # start HTTP API
voicegw dashboard                        # start web UI (port 9090)
voicegw status                           # show provider status

# Dashboard frontend (src/dashboard/frontend/)
npm install && npm run dev               # dev server
npm run build                            # production build

# Docker
docker compose up -d                     # API + Dashboard
docker compose --profile local up -d     # + Ollama
```

## Architecture

**Request flow:** User code → `Gateway.stt()`/`llm()`/`tts()` → Router → Provider → Middleware pipeline (cost tracking, latency, rate limiting, fallback) → SQLite storage → Dashboard reads stored data.

**Core (`src/voicegateway/core/`):**
- `gateway.py` — Main orchestrator, entry point for all requests
- `config.py` — YAML parser with `${ENV_VAR}` substitution
- `router.py` — Resolves `provider/model` strings to provider instances
- `registry.py` — Lazy provider factory (instantiates on first use)
- `model_id.py` — Parses `provider/model` format strings

**Providers (`src/voicegateway/providers/`):** Each extends `BaseProvider` from `base.py`. 11 implementations covering cloud and local models.

**Middleware (`src/voicegateway/middleware/`):** Cost tracking, latency monitoring, rate limiting, request logging, fallback chains. All wrap provider calls. v0.6.0 guardrails are LLM-side only: `InstrumentedLLM._apply_guardrails` uses `middleware/guardrails.py` and `middleware/guardrail_prompts/` to inject the guardrail prompt/tool, reject reserved tool-name collisions, and write fired/bypassed audit rows.

**Storage (`src/voicegateway/storage/`):** SQLite backend with `RequestRecord` dataclass plus guardrail policy snapshots and `guardrail_events` audit rows. Includes SQL views for daily costs and per-project aggregation.

**Dashboard API (`src/dashboard/api/main.py`):** FastAPI with endpoints at `/api/status`, `/api/models`, `/api/costs`, `/api/projects`, `/api/logs`, `/api/metrics`, `/api/projects/{project_id}/guardrails`, `/api/guardrails/events`, and `/api/guardrails/aggregate`. Guardrail endpoints persist project policy overlays and query audit aggregates through the storage repos.

**Dashboard (`dashboard/`):** FastAPI backend (`api/`) + React/TypeScript/Vite frontend (`frontend/`). Uses Recharts for visualization. Neo-Brutalism design aesthetic.

**Public API:** `voicegateway/__init__.py` exports `Gateway`, `ModelId`, `GatewayConfig`.

## Key Patterns

- **Async throughout** — all DB, HTTP, and provider operations use async/await
- **Framework-agnostic**: install provider plugins in your own agent (livekit.plugins.* / pipecat.services.*), not as VoiceGateway extras. VG meters the native instances you pass to attach()/guard() and prices by model_id via voice-prices.
- **Config format** — YAML at `voicegw.yaml`, env vars via `${VAR_NAME}` syntax
- **pytest-asyncio** — `asyncio_mode = "auto"` in pyproject.toml, no manual `@pytest.mark.asyncio` needed
- **Test fixtures** in `src/voicegateway/tests/conftest.py` set fake API keys for all providers
