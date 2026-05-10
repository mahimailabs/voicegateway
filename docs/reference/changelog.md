# Changelog

All notable changes to VoiceGateway are documented here. This project follows [Semantic Versioning](https://semver.org/) and [Conventional Commits](https://www.conventionalcommits.org/).

## v0.1.0 -- 2026-05-10

**Daemon-first onboarding.** v0.1.0 is the operational substrate that makes v0.0.5's parity claim deliverable to anyone who isn't mahimairaja. From a fresh machine to first inference call: one curl command, a five-question wizard, an OS-native daemon, and a dashboard row inside 60 seconds (excluding the time it takes to fetch your provider API key). Adds the daemon machinery (LaunchAgent / systemd `--user` / Scheduled Task), the `voicegw onboard` wizard, lifecycle commands, a ten-check `voicegw doctor`, and a read-only `voicegw migrate` for upgrade verification. v0.0.5's public API and storage layout are preserved verbatim per design decision 2: the canonical config home stays at `~/.config/voicegateway/`.

### Added

- **One-line installer** (`install.sh`). Curl-bash one-liner that detects OS (macOS / Linux / WSL), refuses cleanly if Python 3.11+ is missing (does not auto-install Python; package-manager pointers instead), bootstraps `pipx` via the OS package manager when running as root or via `pip --user` otherwise, and runs `pipx install voicegateway[cloud,dashboard]`. Detects an existing v0.0.5 install and offers `pipx upgrade` plus auto-runs `voicegw migrate` for verification. Container test (`tests/cli/test_install_script.sh`) runs against Ubuntu 24.04, Debian 12, and Fedora 40 in CI via `.github/workflows/install-script.yml`. Implements REQ-VG-ONBOARD-001.
- **`voicegw onboard` wizard.** Five questions: project name (default `default`), provider (default `openai`), API key (no default, hidden input), port (default `8080`), install daemon (default yes). Real-time provider key validation against the upstream API with a 5-second timeout (fail-soft on timeout per REQ-VG-ONBOARD-002.2). Clean Ctrl+C cancellation with byte-for-byte rollback of any pre-existing config. End-of-wizard summary shows project / provider / port / daemon status / dashboard URL. Optionally runs `voicegw smoke-test` as the first-call moment (REQ-VG-ONBOARD-005). Implements REQ-VG-ONBOARD-002.
- **Daemon facade and three OS backends.** `voicegateway/cli/daemon/__init__.py` defines a `DaemonBackend` Protocol (install / uninstall / start / stop / restart / status / logs); `DaemonManager` picks the backend by `sys.platform`. Backends: `macos.py` (LaunchAgent at `~/Library/LaunchAgents/ai.openrtc.voicegateway.plist`, wraps `launchctl bootstrap/bootout/print/kickstart`), `linux.py` (systemd `--user` unit at `~/.config/systemd/user/voicegateway.service`, wraps `systemctl --user` + `journalctl --user-unit`), `windows.py` (Scheduled Task via `schtasks.exe` with a Start Menu Startup-folder `.lnk` fallback for locked-down boxes). Templates at `voicegateway/cli/daemon/templates/launchagent.plist` and `systemd.service` rendered via `string.Template`. Plist + unit files written with mode 0644. Implements REQ-VG-ONBOARD-003.
- **Lifecycle commands.** `voicegw start`, `voicegw stop`, `voicegw restart`, `voicegw daemon-logs`, `voicegw uninstall-daemon`. Each delegates to the platform backend; uninstall-daemon explicitly states what was preserved (config file, call DB, encrypted managed_providers rows) and the documented manual cleanup command (`rm -rf ~/.config/voicegateway/`) per design decision 5. `voicegw daemon-logs --tail N` (default 100, `-n` short flag) routes through the OS-native log surface so you don't need to remember which tool each platform uses: `log show` on macOS, `journalctl --user-unit voicegateway` on Linux, the per-user log file under `%LOCALAPPDATA%` on Windows. Empty output prints a "no daemon logs yet" hint instead of a blank screen; backend errors exit with code 1. AC-VG-ONBOARD-004.2 timing assertion caps the cli surface at 1.0s with a mocked manager. Implements REQ-VG-ONBOARD-004.
- **`voicegw doctor`** with ten checks rendered as a numbered Rich punch list: Python version, pipx installed, daemon registered, daemon running, port conflict, provider configured, provider key valid, recent error count, dashboard reachable, MCP responsive. Three-status model (ok / fail / skip): skip is the documented non-blocking status for "this check doesn't apply right now" (e.g., daemon-running when not registered, MCP probe under stdio). Every fail row carries a specific fix action (AC-VG-ONBOARD-006.2): no stack traces, no bare "see docs" pointers. Implements REQ-VG-ONBOARD-006.
- **`voicegw migrate`** read-only detection. Verifies a v0.0.5 install at the canonical config home (yaml parseable, SQLite db readable, managed_providers keys decrypt under the current `VOICEGW_SECRET`, daemon registration status). No copy step because v0.1.0 keeps the v0.0.5 path (design decision 2). The output ends with an explicit "this command is read-only; no files were written; your v0.0.5 install is unchanged" footer. Atomic-write seam (`_atomic_write_text`) ships ready for the first schema bump that introduces a write. Implements REQ-VG-ONBOARD-007.
- **`/get-started` landing page** (`docs/get-started.md`). 60-second above-fold install + wizard + three-step preview; below-fold troubleshooting box covering Python missing, pipx missing, provider key invalid. Implements REQ-VG-ONBOARD-008.
- **`docs/migration/from-v0.0.5.md`.** One-page migration guide covering the `voicegw status` reorder, the new daemon, doctor, migrate, and the unchanged v0.0.5 surface (every existing import path keeps working).

### Changed

- **`voicegw status`** now renders the daemon section FIRST, then the provider section (design decision 4). Two sections are independent: a missing daemon backend prints a yellow "Daemon status unavailable" line and the provider section still renders.
- **`voicegateway/cli`** is now a package, not a single file. The original `voicegateway/cli.py` (1165 LOC) is split into focused submodules per command (`init.py`, `serve.py`, `projects.py`, `smoke_test.py`, etc.) plus `_app.py` (Typer app + Rich console + `--version` callback) and `_helpers.py` (`_load_gateway`, `_parse_iso_date_arg`). The `from voicegateway.cli import app` contract is preserved verbatim; the `voicegw = "voicegateway.cli:app"` console-script entry point is unchanged.
- **`pyproject.toml` cloud extras** add `psutil>=5.9` (port + process inspection in `voicegw doctor`) and `platformdirs>=4.0` (OS-canonical config home resolution for the daemon backends). Both deps were pre-approved in the v0.1.0 spec.
- **`README.md`** Quick Start section leads with the curl-bash one-liner; manual `pipx install` and `pip install` flows ship as the second and third snippets under Option 1 for users who prefer them.
- **Public command surface** grew from 13 commands (v0.0.5) to 21 (v0.1.0): adds `onboard`, `start`, `stop`, `restart`, `daemon-logs`, `uninstall-daemon`, `doctor`, `migrate`. The v0.0.5 set is unchanged; the back-compat assertion test (`tests/cli/test_imports.py`) tracks the v0.0.5 + v0.1.0 sets independently so a future regression that drops a v0.0.5 command trips immediately.

### Migration

See [docs/migration/from-v0.0.5.md](../migration/from-v0.0.5.md). Short version: every v0.0.5 import keeps working unchanged. Run `voicegw migrate` to verify the existing install carries over, then `voicegw onboard --install-daemon` to register the per-user daemon. The canonical config home (`~/.config/voicegateway/`) is preserved verbatim; nothing in your existing yaml or SQLite database needs to move.

### Out of scope (deferred)

- The metrics-dashboard view (originally v0.0.6) is paused until v0.1.0 adoption proves the operational hypothesis. Will return as v0.2.0.
- Terminal UI is the v0.1.1 fast-follow.
- Single-binary distribution, auto-update, anonymous telemetry, native Windows installer beyond the Scheduled Task best-effort all stay deferred.

---

## v0.0.5 -- 2026-05-07

**LiveKit Cloud parity.** A drop-in mirror of `livekit.agents.inference` backed by VoiceGateway: change one import line, keep your agent code identical, route through your own provider keys with full cost transparency. Adds session correlation, per-project provider key resolution, five new MCP tools for key management, and a dashboard Providers page.

### Added

- **`voicegateway.inference` module.** Drop-in mirror of `livekit.agents.inference` (LK 1.5.7). `inference.STT`, `inference.LLM`, and `inference.TTS` constructor signatures match LK's verbatim by name, kind, and default — verified by `tests/inference/test_drop_in_compatibility.py` parametrized over all three modalities. Migration is one line: `from livekit.agents import inference` → `from voicegateway import inference`. STT and TTS preserve LK's colon-suffix parsing (language for STT, voice for TTS); LLM does not (Ollama tags like `qwen2.5:3b` survive verbatim). The `api_key` kwarg overrides the project's resolved key for that one instance (escape hatch for testing).
- **Session correlation via `ContextVar`.** Every STT, LLM, and TTS factory call inside one async context shares one `session_id` (`vg-<uuid4>`). The id is read at request time (not construction time) by `InstrumentedSTT/LLM/TTS._log_request` and persisted to `requests.session_id`. The new `sessions` table accumulates `total_cost_usd`, `request_count`, and a comma-separated `modalities` list per session via an SQL UPSERT in the same connection / commit as the requests INSERT for atomicity.
- **`/v1/sessions` and `/v1/sessions/{id}` HTTP endpoints.** Newest-first list with optional `project=` filter; detail returns one row or 404. Modalities surface as a JSON array, not the raw comma-separated string in the table.
- **Per-project provider key resolution.** `voicegw.yaml`'s `projects.<id>.providers.<name>` block now overrides the top-level `providers:` block when set. Resolution order per design.md section 3.3: (1) `inference.set_project(name)` in current context → (2) `VOICEGW_ACTIVE_PROJECT` env var → (3) `default_project` field in voicegw.yaml → (4) hard `ConfigError` if projects are configured but none picked. Soft fallback to `"default"` only when no projects exist (preserves backward compat for pre-v0.0.5 deployments).
- **Five new MCP provider/key tools** in `voicegateway/mcp/tools/providers.py`: `vg_add_provider(project, provider, api_key, base_url=None)`, `vg_remove_provider`, `vg_list_providers(project=None)`, `vg_set_provider_key` (rotation path; errors when row doesn't exist), `vg_test_provider_key` (runs the underlying provider's `health_check`). All keys Fernet-encrypted at rest. The `managed_providers` table gains a nullable `project` column (NULL = legacy global scope; pre-v0.0.5 rows untouched).
- **Dashboard Providers page** (`/providers` in the dashboard frontend). Lists per-project provider keys grouped by project with masked api_key + SourceBadge. Per-row Test/Rotate/Delete buttons with a colored status dot showing the last test result (gray=untested, yellow=testing, green=ok+latency, pink=failed). Add Provider modal: project selector, provider dropdown over the eleven supported providers, masked key input with show/hide toggle, optional base_url, Test Connection button (sentinel-id pattern with cleanup), Save/Cancel.
- **Dashboard backend HTTP endpoint.** `GET /api/providers/by-project[?project=...]` surfaces both YAML `projects.<id>.providers` entries and DB-managed managed_providers rows where project IS NOT NULL. YAML wins on collision (matches ConfigManager.load_merged precedence). api_key always masked. `POST /v1/providers` and `PATCH /v1/providers/{id}` honor an optional `project` field; PATCH preserves the existing project on rotation unless explicitly overridden.
- **`livekit-agents` pin range** tightened to `>=1.5,<1.7` in pyproject.toml, gating the supported LK version surface around the inference signatures captured in the drop-in compat test. Quarterly bump cadence is on the v0.0.6+ backlog.
- **Migration documentation rewrite.** `docs/migration/from-livekit-inference.md` now leads with the literal one-line diff and includes a 15-line worked example, configuration walkthrough, session correlation explainer, cost comparison, four documented limitations, and three troubleshooting items.

### Changed

- `voicegw.yaml` schema accepts a top-level `default_project: name` field plus per-project `providers:` blocks (backward compat: pre-v0.0.5 configs without per-project providers continue to load and resolve via the global fallback).
- `RequestRecord.session_id` is a new optional field (default None for legacy callers).
- `CostTracker.create_record` accepts an optional `session_id` kwarg; default None.
- `SQLiteStorage.upsert_managed_provider` accepts an optional `project` kwarg.
- `docs/api/python-sdk.md` reorganized to lead with the `voicegateway.inference` module; the Gateway section follows. A new "Choosing between inference and Gateway" comparison table maps eight common use cases. **No deprecation:** both APIs are first-class.

### Fixed

- **`POST /v1/providers` rejects project-scoped writes that YAML pins.** Before the fix, creating `tony-pizza:openai` returned 200 even when `voicegw.yaml`'s `projects.tony-pizza.providers.openai` already defined a key — the DB row landed but `ConfigManager.load_merged` kept the YAML entry, so the rotation silently never took effect. The handler now mirrors the top-level collision pattern and returns 409 with a message naming the YAML path so the operator knows what to delete.
- **Sessions UPSERT preserves the earliest `started_at` across out-of-order writes.** Requests are logged on completion, so a slow STT call started at T=0 could finish after a fast LLM call started at T=1. The `ON CONFLICT DO UPDATE` clause now takes the MIN, not the first-arrival timestamp, so `/v1/sessions` ordering and duration math reflect actual session start time.
- **`ConfigManager.load_merged` no longer blanks DB-managed project metadata.** The `managed_projects` loop now runs before `managed_providers`, so a project-scoped provider write doesn't replace a real `name`/`description`/`daily_budget`/`tags` row with a `name=project_id` stub. Reserved keys (`api_key`, `base_url`, `_source`) also win over `extra_config` so a malformed entry can't shadow the encrypted-key path or the `db` source tag.
- **`voicegw smoke-test --project <typo>` fails fast.** A typo used to short-circuit through `project or _smoke_active_project(gw)` and surface later as a confusing "no provider key" failure deep in the pipeline. The CLI now validates against `gw.config.projects` up front and prints `Unknown project '<name>'` plus the known list. The smoke sequence is wrapped in `try`/`finally` so `reset_gateway()` always runs; the `--live` health-check loop dedup gap (a duplicated probe across `proj.providers` and `gw.config.providers`) is also closed.
- **Dashboard accessibility on the Sessions page.** Table rows are keyboard-activatable (`tabIndex` + Enter/Space + `role="button"`) and the detail modal has real dialog semantics (`role="dialog"`, `aria-modal`, `aria-labelledby`, document-level Escape handler). Both list and detail fetches use `AbortController` so rapid filter or row toggling can't race a slow earlier response into the current view. The `StalenessBanner` docs path is now a real link to the refresh runbook on GitHub instead of a non-clickable monospace span. (Modal focus trap intentionally not added in this pass; tracked as a v0.0.6+ a11y polish item.)
- **Dashboard backend DRY.** `dashboard/api/main.py` extracts `_LOCAL_PROVIDER_NAMES` so the local-vs-cloud type derivation has a single source of truth across `/api/status` and `/api/providers/by-project`.
- **Doc lints.** Two MD040 unlabeled fences in `docs/cli/smoke-test.md` are tagged `text`; the Mode 3 (Block) example in `docs/examples/budget-enforcement.md` wraps its `await` in `async def main()` + `asyncio.run(main())` so the snippet is copy-paste runnable; the "signature for signature" typo in the homepage feature blurb is fixed.
- **Cartesia `health_check` sends the `Cartesia-Version` header.** The bypass path in `voicegateway/providers/cartesia_provider.py` previously omitted the header, so every `vg_test_provider_key("cartesia")` call and every dashboard "Test" click for Cartesia returned a 400. TTS calls were not affected (livekit-plugins-cartesia adds the header internally); only the direct health-check probe. Pinned to `2025-04-16`, the same value the installed LK plugin uses.
- **Dashboard Providers Delete actually deletes.** The `handleDeleteRow` path in `dashboard/frontend/src/pages/Providers.tsx` issued `DELETE /v1/providers/<id>` without the `?confirm=true` query string the backend requires; the server returned `{would_delete: …}` as a dry-run and the frontend treated that as success, so rows survived the click. The user-facing `window.confirm()` is now followed by a DELETE that includes the flag, so the row really goes away.
- **`InstrumentedSTT/LLM/TTS` subclass the LiveKit base classes** (the AC-2 unblocker). The pre-fix wrapper was a `__getattr__`-style proxy and failed every `isinstance(...)` gate inside `livekit.agents.voice.agent_activity` (16+ checks, including the one in `_start_session` that registers the `metrics_collected` listener). Without that listener, `SpeechHandle` never observed completion and the framework's 5-second `INTERRUPTION_TIMEOUT` cancelled every TTS speech under real audio — verified by side-by-side A/B against `wss://livekit.mahimai.ca` (raw `livekit.plugins.*` agent worked, VG-wrapped agent generated 3+ cancelled speeches per turn). The wrappers now extend `lk_stt.STT` / `lk_llm.LLM` / `lk_tts.TTS`, delegate the abstract method to the wrapped plugin, and forward `metrics_collected` and `error` events through to listeners attached to the wrapper. New `tests/middleware/test_lk_subclass_contract.py` pins both the `isinstance` relation and the event-bridge contract so a future refactor reverting either fails immediately. Smoke-test direct-call path (`_log_request` / `_mark_first_byte`) is unchanged.

### Limitations

- **Session correlation requires the standard async flow.** Factories constructed in separate `asyncio.Task` instances created BEFORE the session opens get their own session ids. Construct factories at session entry, not at module import time. Documented in the migration guide. v0.0.6+ work will surface orphaned requests in the dashboard and may add an explicit `session_id` escape hatch.
- **`api_secret`, `fallback`, and `conn_options`** on the inference factories are accepted for drop-in compat but currently warn-and-ignore (`api_secret` semantically does not apply; the others fall back to `voicegw.yaml`-driven behavior). `voicegateway.inference` users should either drop these parameters or use voicegw.yaml's `fallbacks:` block.

## v0.0.4 -- 2026-05-04

**Cost-tracking foundation rebuild.** (Originally drafted as a parallel `v0.1.0` line during the dual-trunk era; reconciled into the v0.0.x linear sequence as v0.0.4 when the daemon-first v0.1.0 became the canonical 0.1.0 release on 2026-05-10.)
 v0.1.0 ships the `pydantic/genai-prices` integration, modality-aware pricing, fixture-based streaming validation, and reconciliation tooling. The framing throughout README and docs is rewritten from "self-hosted inference gateway" to "modality-aware cost estimation + reconciliation for LiveKit voice agents," matching what the code actually does.

### Added

- **`pydantic/genai-prices` integration as the LLM pricing source.** LLM costs now flow through the upstream `genai-prices` catalog rather than a hand-maintained dict. `pricing_source` attribution surfaces on every recorded request via `RequestRecord.pricing_source`, on the `/v1/costs?include_pricing_source=true` response, and as a column on the dashboard log view.
- **`voicegw export-costs` CLI command.** Writes per-request line items for a date window in CSV (default) or JSON. Optional `--project` filter and `--output FILE` argument.
- **`voicegw reconcile` CLI command.** Compares VG's recorded costs against a provider's normalized usage export. Supports OpenAI, Deepgram, Cartesia. Produces a per-model diff with absolute and percent differences in text (default), CSV, or JSON. Per-provider unit translation handled at the boundary (e.g., Deepgram VG-minutes converted to seconds for the diff against the canonical file's `audio_seconds` column).
- **`/v1/costs` query parameters.** Three new opt-in parameters; default response shape preserved for backward compat.
  - `?per_modality=true` adds an STT/LLM/TTS breakdown.
  - `?include_pricing_source=true` adds the source catalog per `by_model` line (mid-period upgrades surface as comma-joined sources).
  - `?start=YYYY-MM-DD` and `?end=YYYY-MM-DD` ISO date windows. When either bound is set, overrides the legacy `period=today|week|month`. Half-open: start inclusive, end inclusive day (advanced one day for the exclusive upper bound internally).
- **60-day staleness gate** on the local STT and TTS pricing catalogs. CI fails if any entry's `pricing_source_date` is older than 60 days, forcing a manual refresh with each release.
- **Streaming cost-accounting fixture infrastructure (Phase 3).**
  - `scripts/record-streaming-fixtures.py` records the six minimum Phase 3 fixtures end-to-end across three providers and two modes each: OpenAI `gpt-4o-mini` LLM batch + stream, Deepgram `nova-3` STT batch + stream, Cartesia `sonic-3` TTS batch + stream. Default-deny gating: no flags prints a recording-disabled banner; `--record` alone prints a per-fixture cost estimate; `--record --confirm` actually hits the API. A `--all` flag runs all six sequentially with a single `--confirm` and a ~$0.013 aggregate estimate.
  - `tests/fixtures/streaming/_schema.py` defines the `StreamingFixture` pydantic v2 model that locks the fixture JSON shape (metadata block + `request` + `response_stream` + `provider_reported_usage` + `expected_cost_usd`). `_loader.py` exposes `load_fixture` / `discover_fixtures` / `parse_fixture_filename` for tests to consume; filenames use the locked `<provider>_<model>_<modality>_<mode>_<YYYY-MM-DD>.json` convention.
  - `tests/test_streaming_cost_accounting.py` parametrizes per fixture and asserts three things each: unit-count consistency between `provider_reported_usage` and the recorded `response_stream`, cost calculation matching `expected_cost_usd` (both quantized to 8 decimal places), and TTFB hook behavior on stream fixtures. Tests skip cleanly when no fixtures are committed and activate automatically when they land.
  - `scripts/README.md` documents per-fixture cost expectations, env vars, recovery commands, and operational warnings.
- **TTFB hook contract tests.** `tests/middleware/test_instrumented_provider.py` covers `_InstrumentedBase._mark_first_byte` (initial state, idempotency, log_request semantics, proxy + storage paths) so future refactors that break the manual hook fail tests before they ship. `tests/test_ttfb_hook_coverage.py` extends this per-modality (STT, LLM, TTS) and gates against `wrap_provider`'s dispatch table so a future modality cannot land without a TTFB hook reachable from production.
- **Cost-tracking architecture page** at `docs/architecture/cost-tracking.md`. Documents the pricing layer, per-request flow through `_InstrumentedBase`, and the substitute-validation strategy honestly (including its limits: replay does not catch real-time streaming chaos, provider-side correctness, or end-to-end LiveKit session bugs).
- **LiveKit FallbackAdapter integration guide** at `docs/examples/livekit-fallback-adapter.md`. Recommended composition pattern: VG providers wrapped in LiveKit's `FallbackAdapter` for runtime fallback. Each attempt is logged separately so cost tracking still records the right thing.
- **Cost reconciliation walkthrough** at `docs/guide/cost-reconciliation.md`. When-to-reconcile triggers, three-step workflow, diff interpretation, per-modality drift tolerance table.
- **Per-provider reconcile schema reference** at `docs/reference/reconcile-formats.md`. Canonical CSV/JSON shape per provider plus inline Python conversion snippets from each provider's native dashboard export.
- **Decision Tree** at `docs/guide/decision-tree.md`. Honest matrix for when VG fits versus LiteLLM, OpenRouter, Cloudflare AI Gateway, hosted multi-tenant solutions.

### Changed

- **Framing throughout README and docs.** Hero, features, and decision flows rewritten to lead with the LiveKit-voice-agent positioning. Generic "self-hosted inference gateway" framing dropped per the audit (priming readers for LiteLLM-style scope made them bounce when they found a LiveKit plugin factory).
- **`docs/migration/from-litellm.md`** rewritten to acknowledge LiteLLM has STT and TTS endpoints (live since early 2026). Reframed from competitive ("we're better") to complementary ("LiteLLM for general LLM gateway use; VG purpose-built for LiveKit voice agents").
- **LLM pricing maintenance** moved upstream to `pydantic/genai-prices`. The internal LLM rates dict and the legacy `PRICING` / `get_pricing()` shims are removed entirely (with `BaseProvider.get_pricing` along with them); call `voicegateway.pricing.catalog.calculate_cost(modality, model, ...)` instead.
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
- **`scripts/record-streaming-fixtures.py --all` mutex bypass** (Phase 3 Codex adversarial review). The `--all` branch advertised mutual exclusivity with `--provider`, `--modality`, `--model`, `--mode`, but the runtime check omitted `--mode`; because `--mode` defaulted to a truthy `"batch"`, an explicit `--record --all --mode stream` would silently record all six fixtures and bill the operator outside the apparent command intent. `--mode` now defaults to `None` so it only registers when passed; the `--all` mutex check enumerates all four narrowing flags and `parser.error()`s naming each offender. Three regression tests pin the contract.
- **Replay suite fail-closed contract tied to a `PLACEHOLDER.md` marker** (Phase 3 Codex adversarial review). The previous fail-closed contract blocked CI on infrastructure-only branches because no fixtures had landed yet, conflating "fixtures intentionally pending" with "inconsistent state." The replacement three-state contract uses `tests/fixtures/streaming/PLACEHOLDER.md` as the explicit marker: fixtures present runs them; empty + marker emits a documented skip; empty + no marker fails loudly. The dangerous Codex case (silent skip masquerading as pass) remains caught.

### Disclosed

- **v0.1.0 cost tracking is validated against fixture-recorded provider responses, not against real production traffic.** The replay tests cover the canonical paths but are not exhaustive. Reconcile your numbers against your provider invoice during the first 30 days of operation. Subsequent reconciles are spot-checks (after rate changes, before client invoicing milestones, when divergence exceeds the per-modality tolerance).
- **LLM cost is an estimate via `pydantic/genai-prices`** (catalog version surfaced on each record's `pricing_source`). Estimates may drift up to ~5% from a provider invoice. STT and TTS rates come from the local catalog with a 60-day staleness gate; expected drift is lower (~1-2%). For FinOps-grade accuracy, run `voicegw reconcile` and treat the provider invoice as the cost-of-record.
- **Streaming-fixture recordings remain blocked on real provider API access.** The recorder script, the schema, the loader, and the parametrized replay tests all ship in v0.1.0; the actual recorded fixtures are deferred to operator-side work because they need provider API keys and budget. The replay test suite activates automatically when fixtures land at `tests/fixtures/streaming/<provider>_<model>_<modality>_<batch|stream>_<date>.json`. See `tests/fixtures/streaming/PLACEHOLDER.md` for the runbook (delete that file in the same commit that commits the fixtures).
- **The substitute-validation strategy has known limits.** Fixture replay catches structural bugs (recorder normalization, provider schema drift, off-by-one counting, TTFB hooks not firing) but does NOT catch real-time streaming chaos (network jitter, partial chunks split across packets, out-of-order delivery), provider-side correctness (the suite trusts the provider's reported usage), or end-to-end LiveKit session bugs (the wrappers are tested in isolation, not as part of an `AgentSession`). The CHANGELOG line above ("validated against fixture-recorded responses, not against real production traffic") is the literal description of what shipped.
- **The wrapper has no production stream interceptor in v0.1.0.** `_InstrumentedBase` exposes `_mark_first_byte` and `_log_request`, and the streaming-validation suite + TTFB-hardening suite both exercise them, but no production code path fires them today. The replay test suite's unit-counting assertion is therefore at the structural-integrity layer (recorder consistency) rather than the literal "wrapper accumulator" layer the design originally imagined. Wiring a production stream interceptor is a v0.0.5+ task.
- **`v0.1.0-phaseN` ceremonial git tags were not used during development.** `hatch-vcs` rejects non-strict-semver tags; phase boundaries are captured in the journal entries (`.agents/JOURNAL.md`) and the chore(verify) commits on the `feat/cost-track-rebuild` branch.

---

## v0.0.x baseline (before the rebuild)

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
