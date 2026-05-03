# Changelog

All notable changes to VoiceGateway are documented here. This project follows [Semantic Versioning](https://semver.org/) and [Conventional Commits](https://www.conventionalcommits.org/).

## v0.1.0 -- 2026-05-04

**Cost-tracking foundation rebuild.** v0.1.0 ships the `pydantic/genai-prices` integration, modality-aware pricing, fixture-based streaming validation, and reconciliation tooling. The framing throughout README and docs is rewritten from "self-hosted inference gateway" to "modality-aware cost estimation + reconciliation for LiveKit voice agents," matching what the code actually does.

### Added

- **`pydantic/genai-prices` integration as the LLM pricing source.** LLM costs now flow through the upstream `genai-prices` catalog rather than a hand-maintained dict. `pricing_source` attribution surfaces on every recorded request via `RequestRecord.pricing_source`, on the `/v1/costs?include_pricing_source=true` response, and as a column on the dashboard log view.
- **`voicegw export-costs` CLI command.** Writes per-request line items for a date window in CSV (default) or JSON. Optional `--project` filter and `--output FILE` argument.
- **`voicegw reconcile` CLI command.** Compares VG's recorded costs against a provider's normalized usage export. Supports OpenAI, Deepgram, Cartesia. Produces a per-model diff with absolute and percent differences in text (default), CSV, or JSON. Per-provider unit translation handled at the boundary (e.g., Deepgram VG-minutes converted to seconds for the diff against the canonical file's `audio_seconds` column).
- **`/v1/costs` query parameters.** Three new opt-in parameters; default response shape preserved for backward compat.
  - `?per_modality=true` adds an STT/LLM/TTS breakdown.
  - `?include_pricing_source=true` adds the source catalog per `by_model` line (mid-period upgrades surface as comma-joined sources).
  - `?start=YYYY-MM-DD` and `?end=YYYY-MM-DD` ISO date windows. When either bound is set, overrides the legacy `period=today|week|month`. Half-open: start inclusive, end inclusive day (advanced one day for the exclusive upper bound internally).
- **60-day staleness gate** on the local STT and TTS pricing catalogs. CI fails if any entry's `pricing_source_date` is older than 60 days, forcing a manual refresh with each release.
- **Streaming cost-accounting fixture infrastructure.** `scripts/record-streaming-fixtures.py` records provider responses (OpenAI LLM batch + stream working end-to-end; Deepgram and Cartesia stubs). `tests/test_streaming_cost_accounting.py` replays fixtures through VG's pricing catalog with a parametrize-or-skip pattern: cost-calculation contract tests activate automatically when fixtures land.
- **TTFB hook contract tests.** `tests/middleware/test_instrumented_provider.py` covers `_InstrumentedBase._mark_first_byte` (initial state, idempotency, log_request semantics) so future refactors that break the manual hook fail tests before they ship.
- **LiveKit FallbackAdapter integration guide** at `docs/examples/livekit-fallback-adapter.md`. Recommended composition pattern: VG providers wrapped in LiveKit's `FallbackAdapter` for runtime fallback. Each attempt is logged separately so cost tracking still records the right thing.
- **Cost reconciliation walkthrough** at `docs/guide/cost-reconciliation.md`. When-to-reconcile triggers, three-step workflow, diff interpretation, per-modality drift tolerance table.
- **Per-provider reconcile schema reference** at `docs/reference/reconcile-formats.md`. Canonical CSV/JSON shape per provider plus inline Python conversion snippets from each provider's native dashboard export.
- **Decision Tree** at `docs/guide/decision-tree.md`. Honest matrix for when VG fits versus LiteLLM, OpenRouter, Cloudflare AI Gateway, hosted multi-tenant solutions.

### Changed

- **Framing throughout README and docs.** Hero, features, and decision flows rewritten to lead with the LiveKit-voice-agent positioning. Generic "self-hosted inference gateway" framing dropped per the audit (priming readers for LiteLLM-style scope made them bounce when they found a LiveKit plugin factory).
- **`docs/migration/from-litellm.md`** rewritten to acknowledge LiteLLM has STT and TTS endpoints (live since early 2026). Reframed from competitive ("we're better") to complementary ("LiteLLM for general LLM gateway use; VG purpose-built for LiveKit voice agents").
- **LLM pricing maintenance** moved upstream to `pydantic/genai-prices`. The internal LLM rates dict has been removed from the active code path; legacy `PRICING` and `get_pricing()` remain as deprecated shims for the existing test surface and will be removed in v0.2.
- **`docs/guide/first-agent.md`** gains an explicit "LiveKit Server Setup" prerequisites section before VG steps so users do not get stuck on `ConnectionError`. Covers both LiveKit Cloud and self-hosted `livekit-server` paths.
- **Runtime-fallback claims softened.** Audit C1/H5/L2: prior README and docs language implied automatic mid-call provider switching. Reframed to resolver-time-only with pointers to the FallbackAdapter integration guide for the actual runtime-fallback story.
- **Model-id sweep across docs (Phase 1.3.5c).** STT and TTS model IDs aligned to the local catalog (`whisper/large-v3` and `whisper/base` to the `local/` prefix; `kokoro/default` to `local/kokoro`). LLM-side IDs deferred to a v0.1.x sweep once `genai-prices` upstream resolves them naturally.
- **Coverage gate raised to 75%** in `pyproject.toml` (was 70% in v0.0.x). Phase 1.5 verification + Phase 4 verification both meet the gate.

### Fixed

- **`groq/llama-3.1-8b` $0.0 pricing placeholder** (audit C2). The example YAML now uses Groq's canonical `-instant` and `-versatile` suffixed model IDs that `genai-prices` recognizes; bare-name lookups fall through to the no-silent-zero contract (warn + record $0).
- **Dashboard frontend title** (audit C4) at `dashboard/frontend/index.html` corrected from "LiveKit Inference Gateway" to VoiceGateway branding.
- **SQLite backup advice** (audit C3) at `docs/reference/faq.md:175` corrected to the WAL-aware `sqlite3 .backup` command.
- **`VOICEGW_ENCRYPTION_KEY` typo** (audit H4) in `docs/reference/troubleshooting.md` corrected to the canonical `VOICEGW_SECRET`.
- **Broken `VoiceAssistant` import** (audit M4) in `docs/examples/fallback-chains.md` rewritten to the AgentSession idiom used in `examples/basic_agent.py` (the prior `from livekit.agents.voice_assistant import VoiceAssistant` is broken on `livekit-agents>=1.5.0`).
- **FAQ accuracy claims** (audit H2/H3/M1/M2): test coverage figure refreshed; perf numbers softened from unbacked specifics; multi-instance scaling caveat with budget-cache divergence note added; Postgres "planned" tightened to "v0.3+ scope."
- **LiveKit Cloud Inference cost-comparison table** (audit H6) in `docs/migration/from-livekit-inference.md` gained a snapshot date and dashboard cross-reference.

### Disclosed

- **v0.1.0 cost tracking is validated against fixture-recorded provider responses, not against real production traffic.** The replay tests cover the canonical paths but are not exhaustive. Reconcile your numbers against your provider invoice during the first 30 days of operation. Subsequent reconciles are spot-checks (after rate changes, before client invoicing milestones, when divergence exceeds the per-modality tolerance).
- **LLM cost is an estimate via `pydantic/genai-prices`** (catalog version surfaced on each record's `pricing_source`). Estimates may drift up to ~5% from a provider invoice. STT and TTS rates come from the local catalog with a 60-day staleness gate; expected drift is lower (~1-2%). For FinOps-grade accuracy, run `voicegw reconcile` and treat the provider invoice as the cost-of-record.
- **Phase 3.2 streaming-fixture recordings remain blocked on real provider API access.** The recorder script and the replay test infrastructure ship in v0.1.0; the actual recorded fixtures are deferred to operator-side work because they need provider API keys and budget. The replay test suite activates automatically when fixtures land at `tests/fixtures/streaming/<provider>_<model>_<modality>_<batch|stream>_<date>.json`.
- **`v0.1.0-phaseN` ceremonial git tags were not used during development.** `hatch-vcs` rejects non-strict-semver tags; phase boundaries are captured in the journal entries (`.agents/JOURNAL.md`) and the chore(verify) commits on the `feat/cost-track-rebuild` branch.

---

## v0.0.x baseline (prior to the rebuild)

The features below shipped in the v0.0.x line and carry forward unchanged into v0.1.0. They predate the cost-tracking foundation rebuild and are listed here for completeness.

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
- **Fallback chains** -- per-modality resolver-time fallback (try the next model if the primary fails to resolve at agent startup; not a runtime/mid-call switch)
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
