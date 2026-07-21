# Changelog

All notable changes to VoiceGateway are documented here. This project
follows [Semantic Versioning](https://semver.org/) and
[Conventional Commits](https://www.conventionalcommits.org/).

## Unreleased

### Changed

- **`onboard` and `smoke-test` are framework-agnostic now.** Both commands still
  assumed the removed config-driven provider/model pipeline. `onboard` asked you
  to pick a cloud provider and wrote its API key to `voicegw.yaml` in plaintext
  with no `models:` block; the old `smoke-test` then tried to exercise that
  pipeline, skipped everything (no models), and false-failed its session check.
  - **`onboard`** is now a four-question wizard (project, storage, port, daemon).
    It writes no `providers:` block and no key, prints the one line to add to
    your agent (`voicegateway.attach(session, project=...)`) plus the fleet
    collector env vars, and ends by running `check`.
  - **`smoke-test` is renamed to `check`** (the old name stays as a hidden,
    deprecated alias). `check` verifies the path that matters in the
    framework-agnostic model: it drives one synthetic instrumented request and
    asserts a request row + a session row land in storage. No providers, no
    network. It passes on a provider-less config, the case the old command got
    wrong.

### Fixed

- **The CLI no longer requires the livekit runtime to import.** The old
  `smoke_test` helper imported the instrumented middleware (which pulls livekit)
  at module load, so `voicegw` required livekit even for commands that do not use
  it. `check` imports that middleware lazily.
- **The daemon now serves the config you onboarded with.** The installed service
  ran `voicegw serve` with no `-c`, so it fell back to the config search path;
  when you onboarded to `~/.config/voicegateway/voicegw.yaml` (or an explicit
  path) the daemon could not find it, crash-looped under KeepAlive, and left
  nothing on the serve port (`doctor` reported "Daemon running: FAIL"). The
  daemon install now threads the onboarded config path into the launch command
  (`serve -c <path>`) across the macOS, Linux, and Windows backends.
- **Re-installing the daemon is idempotent.** On macOS a second
  `onboard --install-daemon` hit `launchctl bootstrap`'s EIO ("already loaded")
  and aborted, and a crash-looping daemon could be wedged in launchd's throttle
  state. Install now boots out any prior registration before bootstrapping the
  refreshed plist, so re-running onboard always lands the new config on a clean
  slate.

The `/openorca/*` runtime contract gains the pieces a live fleet console needs,
and the engine now serves that console itself at `/console`.

### Added

- **OpenOrca fleet console at `/console`.** A standalone React 19 + Tailwind v4
  SPA (built from `src/dashboard/console`) that renders `<OpenOrcaDashboard
  mode="runtime">` against this server's own `/openorca/*` endpoints. `voicegw
  serve` mounts it when built. Kept separate from the React 18 dashboard because
  `@openorca-ui/react` peers on React 19 and ships source Tailwind v4.
- **Real intervention-resolve endpoint.** `POST /openorca/interventions/resolve`
  validates the intervention id and action (`approve` / `deny` / `later`),
  tenant-scopes the request, persists the resolution with a TTL, and publishes a
  tenant-scoped `intervention.resolved` event. Resolved interventions drop out of
  the snapshot.
- **Snapshot enrichment.** The mapper folds live sessions into agent tasks and
  guardrail events into interventions per project, and rolls them into
  `fleetHealth`, so the snapshot reflects real call and guardrail state.

### Packaging

- The wheel and Docker image now build and bundle the console SPA
  (`build_wheel.sh` stages `_console_dist`; the Dockerfile adds a console builder
  stage), so `pip install voicegateway` and the published image both serve
  `/console`.

## v0.9.2: the Postgres fleet collector actually works

The Postgres collector backend carried several SQLite-only SQL constructs that
crashed the server on startup or on first ingest. They are fixed and now covered
by a CI job that boots the collector against a real Postgres.

### Fixed

- **Ambiguous `ON CONFLICT` columns.** The project and session upserts referenced
  bare existing-row columns (`COALESCE(excluded.x, x)`), which Postgres rejects as
  ambiguous between the target table and `excluded`. They are now table-qualified
  (`managed_projects.x`, `sessions.x`), which both SQLite and Postgres accept.
- **`GROUP_CONCAT` in the cost summary.** Replaced with the dialect-appropriate
  aggregate (`STRING_AGG` on Postgres, `GROUP_CONCAT` on SQLite).
- **`datetime('now', ...)` in the virtual-key staleness queries.** Replaced with a
  Postgres-compatible cutoff (`make_interval` / a Python-computed timestamp).

### Added

- **`Postgres collector` CI workflow.** A `dialect` job runs the collector against
  a Postgres service, and an `image-smoke` job builds the published image and
  boots `docker-compose.collector.yml` against Postgres. Together they gate the
  collector on a working Postgres path before release.

## v0.9.1: collector image and Postgres startup fixes

### Fixed

- **The core Docker image boots again.** It shipped without the hatch-vcs
  generated `_version.py` (the runtime copied the raw source over the installed
  package), so `import voicegateway` crashed on startup. The generated file is now
  baked into the image.
- **Postgres engine event-loop crash.** `Gateway.__init__` runs async startup
  through several short-lived `asyncio.run()` loops; asyncpg binds connections to
  their creating loop, so a pooled connection was reused across loops and crashed.
  The Postgres engine now uses `NullPool`.

## v0.9.0: per-session cost tracking for OpenRTC multi-agent workers

### Added

- **`voicegateway.openrtc.VoiceGatewayObserver`: one-line cost tracking for
  [OpenRTC](https://github.com/mahimailabs/openrtc) workers.** OpenRTC runs many
  LiveKit voice agents in a single worker process. This observer implements
  OpenRTC's `SessionObserver` protocol and drives `voicegateway.attach()` for
  every session, so a whole multi-agent worker gets per-call STT, LLM, and TTS
  cost tracking by passing one argument:
  `AgentPool(observers=[VoiceGatewayObserver(project="prod", collector_url=...,
  virtual_key=...)])`. Attribution is automatic per call: `agent_id` from the
  resolved agent name, `tenant_id` from room or job `metadata["tenant"]`, and
  `project` from the observer config. One sink is built lazily per worker and
  shared across all of that worker's sessions. The adapter is duck-typed (no hard
  runtime dependency on `openrtc`, so `import voicegateway.openrtc` works without
  it installed) and picklable for OpenRTC's `process` isolation mode. Install with
  `pip install "voicegateway[openrtc]"` (requires `openrtc>=0.3.0`). See the
  [OpenRTC example](https://docs.voicegateway.dev/examples/openrtc-multi-agent)
  for the full walkthrough.

## v0.8.6: minimal config records to storage so the dashboard works

### Fixed

- **`voicegw init` enables cost tracking by default again.** The v0.8.4 minimal
  template dropped the `cost_tracking` block, so `voicegw serve` started with
  storage disabled (`gateway.storage is None`) and the dashboard showed no costs,
  sessions, or agents, even though the agent SDK's `voicegateway.attach()` was
  writing to the default SQLite database. The minimal template now enables
  `cost_tracking` at that same default path
  (`~/.config/voicegateway/voicegw.db`), so a first run sees its agents on the
  dashboard with no extra wiring. Existing configs are unaffected; if your server
  was already started with storage off, add `cost_tracking: {enabled: true}` or
  set `VOICEGW_DB_PATH`.

## v0.8.5: fix the Docker image build

The published Docker images for v0.8.3 and v0.8.4 failed to build and were
never pushed. This restores them. The Python package is unchanged from v0.8.4.

### Fixed

- **The Docker image builds again.** v0.8.3 added a wheel `force-include` for the
  Alembic migrations, but neither Dockerfile copied `alembic/` and `alembic.ini`
  into the build stage, so `pip install` failed during metadata generation with
  `Forced include not found: /build/alembic`. Both the core and dashboard
  Dockerfiles now copy the migrations into the builder before installing. PyPI
  was unaffected (it uses a different build path); only the Docker images failed.

## v0.8.4: quieter agents, live dashboard version, friendlier init

Polish from dogfooding the agent SDK. Embedded telemetry no longer floods a
host agent's debug logs, the dashboard reports the real version, the model list
only shows models you can actually call, and `voicegw init` starts minimal.

### Fixed

- **Embedded storage no longer floods agent DEBUG logs.** `voicegateway.attach()`
  runs SQLite storage in-process. Under a LiveKit `console`/`dev` run (root
  logger at DEBUG) the `aiosqlite` and `alembic` loggers emitted a line per
  query, burying the agent's own output. `StorageService` now quiets those two
  dependency loggers to WARNING, and only when the caller has not set a level of
  their own (an explicit `aiosqlite=DEBUG` still wins).
- **The dashboard and `/health` report the real version.** The footer pill and
  the `/health` endpoint were hardcoded to `0.5.0`. Both now read the installed
  `__version__` (PEP 440 local build segment stripped), exposed via `/health`
  and the dashboard `/api/status`.

### Changed

- **The dashboard lists only callable models.** `/api/status` now returns models
  whose provider is configured (a cloud API key is set, or it is a local
  provider). The sidebar count and the Models page stop advertising models the
  operator cannot reach.
- **`voicegw init` writes a minimal config by default.** First run gets a short
  STT + LLM + TTS starter (about 35 lines) instead of the 269-line reference.
  Run `voicegw init --full` for the complete annotated config.

## v0.8.3: ship migrations in the wheel

### Fixed

- **The PyPI wheel now ships the Alembic migrations.** `alembic/` and
  `alembic.ini` live at the repo root, outside the `src/` packages, so the
  published wheel carried no migrations and `run_migrations()` failed at runtime
  with `alembic.ini not found` whenever storage initialized (hit by
  `voicegw serve`, `voicegw dashboard`, and `voicegateway.attach()`'s local
  SQLite sink). They are now force-included under the package, so a
  `pip install voicegateway` can build its schema on first run.

## v0.8.2: importable base install

### Fixed

- **`pip install voicegateway` is importable again.** SQLAlchemy and SQLModel
  are pulled in at import time by `voicegateway.models`, and the embedded
  storage that `voicegateway.attach()` writes through needs Alembic, but all
  three lived only in the `server` extra. They are now core dependencies, so a
  base install (the agent SDK use case) no longer fails with
  `ModuleNotFoundError: No module named 'sqlalchemy'`.
- **The `server` extra installs `python-multipart`.** FastAPI's dashboard logo
  upload (an `UploadFile` route) requires it; it was missing, so `voicegw serve`
  warned and the upload endpoint would have failed.

## v0.8.1: Docker fleet collector support

The official Docker image can now run as the Postgres-backed fleet collector.

### Fixed

- **The image ships the migrations.** `alembic.ini` and the `alembic/` tree are
  now copied into the image, so the server builds its schema on first start. It
  could not before (neither was copied in), which left storage broken on a fresh
  container for both SQLite and Postgres.
- **`VOICEGW_DB_URL` enables storage.** Pointing the collector at Postgres with
  `VOICEGW_DB_URL` alone now turns storage on. Previously it also required
  `cost_tracking.enabled` or `VOICEGW_DB_PATH`, so `POST /v1/ingest` returned
  503 and the collector persisted nothing.

### Added

- The Docker image includes the `postgres` extra (asyncpg), so it can run
  against a Postgres collector backend out of the box.
- `docker-compose.collector.yml`: a ready-to-run Postgres + collector stack,
  with a deployment guide in the docs.

## v0.8.0: fleet collector operational hardening

The self-hosted fleet collector becomes safe to run unattended: ingest rate
limiting, data retention, a windowed per-agent dashboard rollup, and the
background workers that keep them fresh. Two latent bugs that left the collector
fragile are fixed along the way.

### Added

- **Ingest rate limiting.** `POST /v1/ingest` enforces a per-caller token bucket
  (keyed by virtual key, then static API key, then client IP). Over-limit
  requests get `429` with a `Retry-After` header; oversized batches get `413`.
  Configured under the new `ingest` block (`requests_per_minute`, `burst`,
  `max_batch_size`).
- **Data retention.** A background worker hard-deletes aged rows per project:
  sessions and their dependent rows (replay, turns, dead-air, guardrail) by
  `ended_at`, and requests by `timestamp`, in batches. Configured under the new
  `retention` block (`default_days`, default 90).
- **Windowed fleet rollup.** A new `agent_observations` table and a 15-minute
  worker pre-aggregate per-agent cost, requests, p95, and error rate over a 24h
  window, so the Agents dashboard list is fast and internally consistent.
- **Background workers wired into the server.** The latency rollup, agent rollup,
  and retention workers now start with the collector. Configured under the new
  `workers` block (`enabled`, `rollup_interval_seconds`,
  `retention_interval_seconds`).

### Changed

- **Ingest rate limiting is on by default** (120 requests per minute per caller).
  A collector already ingesting faster will start receiving `429`s; the library's
  remote sink honors `Retry-After` and retries without dropping the batch. Set
  `ingest.requests_per_minute: 0` or `ingest.enabled: false` to opt out.
- **The Agents dashboard list now covers the last 24 hours** instead of all time.
  The JSON shape is unchanged; cost, requests, p95, and error rate are now
  window-scoped. The per-agent detail view stays all-time.

### Fixed

- **The remote sink no longer drops telemetry under rate limiting.** A `429` is
  treated as backpressure (parse `Retry-After`, clamp to 60s, retry the same
  batch) rather than dropped after a short fixed backoff.
- **Background workers now actually run in production.** The FastAPI lifespan was
  never attached, so the latency-rollup and retention workers were dormant; the
  collector now starts and stops all three workers on boot and shutdown.

## v0.7.0: voice-prices pricing backend

Pricing moves from `pydantic/genai-prices` to
[`voice-prices`](https://github.com/mahimailabs/voice-prices), a fork that
prices all three modalities (LLM, STT, and TTS) from one source.

### Changed

- **Single pricing backend.** LLM, STT, and TTS costs now all resolve
  through `voice-prices`. The hand-maintained local STT/TTS rate catalogs
  are retired; `voice-prices` owns rates and freshness (each entry carries
  `prices_checked` and `pricing_source_url`).
- **Pricing-source attribution.** Cloud-priced records are tagged
  `voice-prices@<version>`; self-hosted (`local/*`, `ollama/*`) models are
  tagged `voicegateway-local`; unknown models stay unpriced. The catalog-only
  `oldest_entry_date` field is dropped from the `/v1/status` and `/api/status`
  responses (`voice-prices` owns freshness).
- **STT and TTS rates** now follow `voice-prices` and may differ from the
  previous local-catalog estimates. Reconcile against your provider invoices.

### Dependencies

- `genai-prices` replaced by `voice-prices>=0.0.8,<0.1`.

## v0.6.0: first public release

The first public release of VoiceGateway. A self-hosted gateway for
LiveKit voice agents that tracks costs per modality (audio-minutes for
STT, tokens for LLM, characters for TTS) and reconciles logged costs
against provider invoices.

### What you get out of the box

- **Drop-in replacement for `livekit.agents.inference`.** Swap one
  import line and your agent code keeps running:
  `from voicegateway.inference import STT, LLM, TTS`. Cost tracking,
  latency monitoring, and per-session correlation happen transparently.
- **Cost tracking per modality.** LLM cost per 1k tokens (prices from
  `pydantic/genai-prices`, 1100+ models). STT cost per audio-minute and
  TTS cost per character (catalog with source-date metadata). Cached
  LLM input tokens are billed at the provider's cache-read discount
  rate (OpenAI 50%, Anthropic ~10%) by surfacing LiveKit's
  `prompt_cached_tokens` through to `genai-prices.cache_read_tokens`.
- **Background daemon.** `voicegw onboard` runs a five-question wizard,
  writes `voicegw.yaml`, registers a user-scoped service (LaunchAgent on
  macOS, `systemd --user` unit on Linux, Scheduled Task on Windows),
  and starts the daemon.
- **Web dashboard and HTTP API on a single port.** The daemon serves
  the React dashboard at `/`, the dashboard API at `/api/*`, and the
  public HTTP API at `/v1/*`. `voicegw dashboard` opens your browser
  at the daemon URL.
- **Reconciliation tooling.** `voicegw export-costs` and
  `voicegw reconcile` compare your logged costs against your provider's
  usage export. Per-row `pricing_source` attribution shows exactly
  which catalog or version priced each call.
- **MCP server for agent-managed config.** Seventeen tools over stdio
  and HTTP/SSE let Claude Code, Cursor, Codex, and Cline manage
  providers, projects, budgets, and queries conversationally.
- **Multi-tenant attribution.** Virtual API keys carry a tenant id so
  sessions auto-tag for per-customer reporting. Virtual keys expose
  their plaintext exactly once at creation and support soft revocation.
- **Cross-modality routing.** Per-session, lowest-predicted-total-
  latency selection of (STT, LLM, TTS) from per-project rosters, with
  observed latency feeding the predictor.
- **White-label branding.** Per-project logo, accent color, and
  product name. The dashboard chrome reflects the brand for users
  scoped to that project.
- **Conversation replay.** Per-modality time-ordered capture of every
  request, with retention windows configurable per project.
- **Guardrails.** Per-project policy overlay (PII categories, action
  enforcement), audit log of fired and bypassed events.

### Install

```bash
curl -fsSL https://voicegateway.dev/install.sh | bash
```

Or:

```bash
pipx install 'voicegateway[cloud,dashboard]'
uv tool install 'voicegateway[cloud,dashboard]'
```

See [Get started](https://docs.voicegateway.dev/get-started)
for the full first-run flow.
