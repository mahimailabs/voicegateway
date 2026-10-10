# Architecture

<!--
How VoiceGateway is built. The tables and lists below were drafted from the code; the lines starting with TODO are yours to write. Product intent lives in PRODUCT.md; visual design lives in DESIGN.md.
Each major choice links to a record in docs/decisions/.
-->

## Overview

TODO: Two or three sentences on the shape of the system, in your words. Name the two halves (what runs inside the user's agent, what runs in `voicegw serve`) and how they meet.

## Components

| Component | Responsibility | Owns |
| --- | --- | --- |
| `inference/session/` | `attach()`: detects LiveKit or Pipecat lazily, subscribes to metrics, captures turns, dead air, tool calls, transcripts and snapshots. `policy.py` holds the named capture policies | Agent-side capture |
| `inference/session/capture.py` | `MetricCapture`: framework metric to `RequestRecord` (audio seconds, tokens, characters) | The record shape |
| `inference/livekit/`, `inference/pipecat/` | Framework-specific `guard()` and the Pipecat `Observer` | Framework seams |
| `inference/pricing/` | `calculate_cost_detail()`: prices by modality through voice-prices; `local/*` and `ollama/*` at $0; unrated matches tagged `voice-prices-unrated` | Cost per record |
| `services/sinks.py` | The write seam: embedded SQLite, remote collector (fleet mode) or ClickHouse | Where records go |
| `accounting/` | Versioned wire contracts (decimal-string money) and `AccountingOutbox`, a restart-safe queue to a collector's `/v1/accounting/usage` | Fleet delivery |
| `billing/` | Rate card, rating, margin reconciliation | Customer-facing price |
| `middleware/` | Cost tracking, rate limit, budget, turns, dead air, state snapshots, background workers (node samples, latency and agent observations) | Cross-cutting runtime behavior |
| `core/` | `Gateway` shared-state container for server, CLI and MCP; YAML config with `${ENV_VAR}`; dependency-injector wiring; canonical provider ids | Wiring and config |
| `models/`, `repository/`, `services/` | SQLModel tables, repositories, `storage_service.py` facade | Persistence |
| `alembic/` (repo root) | Migrations, including the `daily_costs` and `project_daily_costs` views | Schema |
| `server/` | FastAPI app: `/health`, `/v1/*` (`server/api/`), `/api/*` dashboard router, static SPA | HTTP surface |
| `server/mcp/` | MCP server behind `voicegw mcp` (stdio or http) | Agent surface |
| `livekit_diag/`, `fleet/` | LiveKit diagnostics and probes, collector fleet | Layers below inference |
| `src/dashboard/frontend/` | React dashboard SPA, served by the server at `/` | Human UI |
| `site/docs/`, `site/web/` | Docs site (Fumadocs) and landing page (Astro), sharing `site/theme.css` | Public web |
| `ee/` | Enterprise Edition code under `ee/LICENSE`. Empty today | Commercial features |

## Data Flow

Inference path:

1. The user builds an `AgentSession` (LiveKit) or `PipelineTask` (Pipecat) with their own provider plugins and calls `voicegateway.attach(session)`.
2. `attach()` subscribes to the framework's metrics events; `MetricCapture` turns each STT, LLM or TTS metric into a `RequestRecord`.
3. `inference/pricing/` prices the record through voice-prices by model id and modality.
4. A sink writes it: SQLite in-process by default, the collector's ingest API in fleet mode (through `AccountingOutbox`), or ClickHouse.
5. The dashboard (`/api/*`), the `/v1/*` API and the MCP server read what the sinks stored.

Call path (layers below inference):

1. LiveKit webhooks hit `/v1` (`server/api/livekit_webhook.py`) and create `calls` and `call_legs` keyed on the room, with SIP attributes, even when no inference ran.
2. Node samples and agent observations arrive from background workers and correlate to calls by node and time window, never by foreign key.
3. Sessions, turns and requests join to calls through the room.

## Boundaries

- `import voicegateway` imports neither LiveKit nor Pipecat; `attach()` and `guard()` import the framework lazily by target type. `tests/architecture` and `test_framework_neutral.py` pin this.
- The agent side (`attach`, `guard`, sinks) never imports dependency-injector or the server.
- `guard()` wraps one provider for fallback, rate limit and budget and writes no metrics.
- Prices come only from voice-prices or the user's rate card. No price is hardcoded in metering code.
- Dashboard reads live under `/api/*` behind `require_principal`. Every write and ingest route lives under `/v1/*`.
- Every model change ships with an Alembic migration in the same PR, so a fresh install and an upgraded one end with the same schema.
- Capture fails open: an error in metering is logged, never raised into the agent's call path.
- A field that is not observable is not stored. Unknown renders as unknown, not zero.
- Code under `ee/` is the only code that may be commercial; the core must work without it.

## Stack

| Layer | Choice | Decision |
| --- | --- | --- |
| Language | Python 3.11 to 3.13, async throughout | |
| Agent frameworks | LiveKit Agents, Pipecat, OpenRTC, as optional extras | docs/decisions/0002-framework-agnostic-attach.md |
| Pricing | voice-prices (fork of pydantic/genai-prices), `>=0.11,<1` | docs/decisions/0003-pricing-through-voice-prices.md |
| HTTP | FastAPI, one combined server | docs/decisions/0004-one-combined-server.md |
| Persistence | SQLModel and SQLAlchemy 2 async, Alembic; SQLite default, Postgres collector, optional ClickHouse | |
| Wiring | dependency-injector `>=4.49.1,<5` | |
| CLI | Typer and Rich (`voicegw`) | |
| MCP | `mcp` Python SDK (capped below 2), stdio and http | |
| Dashboard | React 18, TypeScript 5, Vite 5, Recharts | |
| Docs and landing | Fumadocs (Next.js static export) and Astro, on Cloudflare | |
| Packaging | uv, hatch, PyPI `voicegateway`, Docker images | |
| License | MIT core, `ee/` commercial | docs/decisions/0001-open-core-license.md |

## Data Model

- `requests`: the atomic metered unit (one STT, LLM or TTS call) with units and cost.
- `sessions`: derived from requests, gains `room_name` and `call_id`.
- `turns`, `transcript_turns`, `tool_calls`, `dead_air_events`, `replay_state_snapshots`: conversation detail per session.
- `calls` and `call_legs`: one call per room, one leg per participant, created by any event (webhook, loadgen, agent).
- `node_samples`, `latency_observations`, `agent_observations`, `agent_probe_results`, `diagnostics_runs`, `workers`: infra, probe and runtime signals, correlated by node and time.
- `managed_projects`, `managed_providers`, `managed_models`, `managed_rate_rules`, `api_keys`, `tenants`, `config_audit_log`: configuration and access.
- `pricing_revisions`, `prepared_pricing_bindings`: which price version rated a record.
- `accounting_*`: usage, ownership, rejections and the outbox shipped to a collector.

The full end-to-end model is in docs/specs/2026-07-29-end-to-end-profiling-scope.md.

## External Services

- **AI providers** (OpenAI, Deepgram, Anthropic, Groq, Cartesia, ElevenLabs, AssemblyAI and others): called by the user's agent, never by VoiceGateway on the metering path. If they are down, the agent fails; VoiceGateway records what happened.
- **voice-prices**: a Python dependency with a bundled catalog, so pricing works offline. An unknown model is tagged unrated rather than failing.
- **LiveKit server and SIP**: webhooks and Prometheus metrics feed the call layers. Without them, the inference layers still work.
- **Cloudflare**: hosts the docs and landing page only.

## Non-Functional Targets

TODO: The numbers V1 must hit, each with how it is measured. Example: "`attach()` adds under 1 ms per turn (measured by a benchmark in tools/benchmarks/)."

## Security and Privacy

- Dashboard and API auth: `vk_` API keys with scopes (`server/api/_deps.py`), or a static key for single-user installs.
- Secrets live in environment variables, referenced from `voicegw.yaml` as `${VAR}`. Never in the repo.
- No audio is stored. Transcripts are stored only under a capture policy that allows them.
- Security reports go through SECURITY.md.

## Deployment

- **Library:** tag `v*` triggers `publish.yml`, which builds the dashboard into the wheel (`tools/scripts/build_wheel.sh`) and publishes to PyPI.
- **Server:** `docker compose up -d` from the repo root, Docker images from `docker-publish.yml`, or Railway via `railway.json` (health check `/health`).
- **Collector:** `collector.sh` or `docker-compose.collector.yml` (Postgres-backed).
- **Sites:** `site.yml` builds and deploys the docs and landing page to Cloudflare.
- Roll back by pinning the previous PyPI version or image tag; migrations are forward-only, so a downgrade needs a database restore.

## Observability

- Server logs through Python `logging`; `/health` for liveness.
- Prometheus exposition in `middleware/prometheus_exposition.py`.
- When metering looks wrong, start with the `requests` rows and their `voice-prices-unrated` tags, then `voicegw reconcile`.
