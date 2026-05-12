# Changelog

All notable changes to VoiceGateway are documented here. This project follows [Semantic Versioning](https://semver.org/) and [Conventional Commits](https://www.conventionalcommits.org/).

## v0.6.0 -- unreleased

**Final v0.1.x re-export shim retirement.** The `voicegateway.combined_server` module was a re-export shim introduced in v0.1.2 to preserve the pre-refactor import path. It was flagged for removal in v0.2.0 but persisted through v0.5.0; v0.6.0 retires it. Callers must use the canonical `voicegateway.server.combined` path.

### Removed

- **`voicegateway/combined_server.py`** re-export shim. The three contract tests that exercised the shim path in `tests/integration/test_v0_1_x_imports.py` were dropped; the canonical-path test was kept and re-headed. `tests/server/test_combined_server.py` and `docker/voicegateway.Dockerfile` (container CMD) were updated to the canonical import.

### Migration

If your code imports from the old shim path, update it:

- `from voicegateway.combined_server import build_combined_app, main`
  → `from voicegateway.server.combined import build_combined_app, main`
- `python -m voicegateway.combined_server`
  → `python -m voicegateway.server.combined`

## v0.5.0 -- 2026-05-12

**Cross-modality routing and white-label branding.** Two capabilities ship together because they target the same agency rung of the buyer ladder. The router runs once per session at session-create time, reads the project's latency budget plus recent observed per-provider latency, and picks the (STT, LLM, TTS) combination from the project's rosters that minimises predicted total latency under budget. White-label branding lets agencies upload a per-project logo, accent color, and product name; the dashboard chrome reflects the brand for users scoped to that project. The picked triple persists on the session row so the dashboard can show what ran and how close the call landed.

### Added

- **Migration 0006** (REQ-VG-ROUTE-001..004) adds five nullable routing columns to `sessions` (`budget_ms`, `budget_ms_used`, `budget_overrun`, `routed_llm`, `routed_tts`), `branding_json` nullable on `managed_projects`, and a new `latency_observations` table with `idx_latency_obs_project_provider` composite index. Idempotent; pre-v0.5.0 rows preserved with NULL.
- **`voicegateway.middleware.router`** (REQ-VG-ROUTE-002): `route_session` picks the lowest-predicted-total triple under budget from the project's rosters. Observed p50 from `latency_observations` wins; missing observations fall back to `voicegateway/core/provider_baselines.json` (10 curated entries with `source_url` + `verified_at`). Caller overrides win for named modalities. Fallback to fastest + `budget_overrun=True` when nothing fits and `fallback_to_fastest=True`; otherwise raises `BudgetExceeded`.
- **`voicegateway.storage.latency_observations_repo`** (REQ-VG-ROUTE-002 AC-4 + REQ-VG-ROUTE-003): `roll_up` aggregates per (project, provider, modality) p50/p95 over a trailing window (default 24h, OQ2 lock); DELETE + INSERT atomic snapshot replaces the prior rollup. `read_all` / `get_for_project` / `get_one` read-side helpers.
- **`voicegateway.middleware.latency_observations_worker`**: asyncio loop mirroring v0.3.0's retention_worker. Wakes every 15 minutes (OQ2 lock), triggers `roll_up`. start/stop/tick_now lifecycle.
- **`set_routing_decision` / `current_routing_decision` ContextVar** in `voicegateway/inference/_session_context.py` plus `attach_session(routed_triple=...)` kwarg threading the triple. `log_request` reads the ContextVar at the sessions UPSERT and stamps `routed_llm / routed_tts / budget_ms / budget_overrun` with COALESCE on conflict (AC-5 immutability).
- **`RoutingConfig`** per-project YAML knob: `routing.budget_ms` (default 1500ms, OQ1 lock), `routing.rosters` (dict: stt/llm/tts -> ordered provider list), `routing.fallback_to_fastest` (default True). Plus **`BrandingConfig`**: `branding.logo_url`, `branding.accent_color`, `branding.product_name` (all nullable). Mirrored across Pydantic schema and runtime dataclass.
- **`SQLiteStorage._validate_branding`** enforces logo_url ≤2048 chars, hex regex `#RGB`/`#RRGGBB`, product_name ≤64 chars, no unknown keys. `upsert_managed_project` accepts a `branding` kwarg; `list_managed_projects` SELECT extended with `branding_json`. COALESCE-protected upsert preserves prior branding on partial updates.
- **Four new dashboard endpoints**: `GET /api/routing/observations[?project=…]`, `GET /api/projects/{id}/branding`, `POST /api/projects/{id}/branding`, `POST /api/projects/{id}/branding/logo` (multipart, Pillow-validated PNG: 256 KB cap + 512×512 + format check; SVG header validated). Static branding mount at `/static/branding/`.
- **`dashboard/frontend/src/pages/Routing.tsx`** with sortable columns (Modality / Provider / p50 / p95 / Samples), hourly auto-refresh, NULL p50 → "no observations yet" (AC-4), optional `?project=…` URL scoping.
- **`dashboard/frontend/src/lib/branding.ts`** applies branding via CSS variables on `document.documentElement` (`--brand-accent`, `--brand-product-name`, `--brand-logo-url`) + favicon + document.title. Read-once-per-mount (OQ5).
- **App.tsx Sidebar** reads the active project's branding once on mount and renders the branded logo image + product name (falling back to "VoiceGateway").
- **SessionDetailModal RoutingStrip** shows the picked triple + budget_ms + budget_overrun chip. Hidden entirely on pre-v0.5.0 sessions.
- **Projects page Brand button** opens a per-project `BrandingModal` with product name input, accent color picker (native `<input type=color>` + hex sync), PNG/SVG upload with yellow-bg preview, Save/Reset/Cancel.
- **App.tsx Routing route + sidebar nav** at `/routing`, positioned between Metrics and Projects per Foundry order.
- **Typed fetchers + types** in `lib/api.ts` and `lib/types.ts`: `RoutedTriple`, `LatencyObservation`, `RoutingObservationsResponse`, `ProjectBranding`, `ProjectBrandingResponse`, `LogoUploadResponse`. `fetchRoutingObservations`, `fetchProjectBranding`, `updateProjectBranding`, `uploadBrandingLogo` (multipart-aware raw fetch preserving the AUTH_REQUIRED contract).
- **`voicegw route show / simulate`** read-only CLI for routing diagnostics. `show` prints rosters + current observations; `simulate` dry-runs the picker with optional per-modality overrides.
- **`voicegw brand set / show / clear`** CLI for scripted provisioning. Hits the same dashboard endpoints as the FE so agencies can roll new brands from scripts. Uses httpx; reads `VOICEGW_API_KEY` for Bearer auth.
- **46 new tests** across `tests/storage/`, `tests/middleware/`, `tests/server/`, `tests/inference/`. Full suite: 1509 → 1555 (+46). Coverage: 86.11% (above 80% Foundry gate).
- **`docs/api/python-sdk.md`** grew a "Cross-modality routing (v0.5.0)" section between v0.4.0 tenant and v0.3.0 replay sections.
- **`docs/guide/agency-quickstart.md`** is the operator walkthrough: configure rosters, verify via CLI, upload branding, share a branded link, watch the Routing view, inspect per-session decisions.
- **`pillow>=10.0`** added to `[project] dependencies` for the logo upload PNG validation path.

### Changed

- **Embedded version strings** bumped from `0.4.0` to `0.5.0` in `voicegateway/server/main.py` (FastAPI ctor + `/health.version`), `dashboard/api/main.py` (FastAPI ctor), `tests/server/test_server.py` (version assertion), `docker/voicegateway.Dockerfile` (`ARG VERSION` in both stages), and the dashboard sidebar pill in `dashboard/frontend/src/App.tsx`.

### Decisions locked (Foundry Open Questions)

- **OQ1 default budget_ms** — 1500 ms. Typical conversational voice-agent target.
- **OQ2 roll-up window + cadence** — 24h rolling window, 15-minute refresh.
- **OQ3 no-observations fallback** — curated `voicegateway/core/provider_baselines.json` with `source_url` per entry.
- **OQ4 logo upload constraints** — 256 KB max, PNG or SVG only, 512×512 px max. Pillow-validates PNG on upload.
- **OQ5 branding cache strategy** — read-once-per-mount. Operators see brand changes on next page load.

### Notes

- **No mid-call routing.** The triple stays fixed for the session's lifetime (AC-5).
- **No adaptive learning.** Static aggregation; no ML on in-call telemetry.
- **No custom-domain dashboard hosting.** White-label sits at the gateway's own host.
- **No per-tenant branding inside one project.** Agencies running multiple downstream tenants in one project share one brand.
- **No email or exported-report branding.** Dashboard chrome only.
- **No cost-aware routing.** v0.5.0 picks on latency.
- **No multi-region routing.**
- **No in-flight budget enforcement.** Budget is a router input at start, not a runtime kill switch.

## v0.4.0 -- 2026-05-11

**Multi-tenant cost attribution.** VoiceGateway now tags every voice session with an optional `tenant_id` so a single deployment can serve many customers and account for each one separately. Three independent surfaces set the tenant: an `attach_session(tenant_id=...)` kwarg for owner-of-AgentSession code, an `inference.set_tenant("…")` ContextVar API, and scoped virtual API keys that auto-attribute at the auth layer. Every cost row, metric row, and replay event for an attributed session lands tagged with the tenant; pre-v0.4.0 rows stay in the "unattributed" bucket (NULL `tenant_id`). The dashboard's existing Costs, Sessions, Metrics, and Replay pages all rescope when a tenant is selected from the new shared `FilterBar`; a new `Virtual Keys` page issues, lists, and revokes keys with a one-time plaintext modal.

### Added

- **Migration 0005** (REQ-VG-TENANT-001 + REQ-VG-TENANT-003): creates the `virtual_keys` table with bcrypt-hashed key storage + visible-prefix index + tenant scope, and adds `tenant_id TEXT` to `sessions`, `requests`, `turns`, `dead_air_events`, and the four `replay_*` tables. Idempotent. The derived-table ALTERs are guarded by a `sqlite_master` presence check (Foundry OQ4) so a v0.0.5-only baseline runs the migration cleanly without erroring on absent v0.2.0 / v0.3.0 tables.
- **`voicegateway.inference._session_context`** gains `set_tenant`, `current_tenant`, and `reset_tenant_id` (REQ-VG-TENANT-001 AC-4). The `tenant_id_ctx` ContextVar propagates the active scope through every gateway call within a session without re-passing. 128-char UTF-8 cap (OQ2 lock) enforced at `set_tenant` time.
- **`voicegateway.storage.virtual_keys_repo`** (REQ-VG-TENANT-003). Flat async function module: `create_virtual_key` (returns plaintext exactly once), `verify` (prefix-scan + bcrypt; revoked keys filtered inside), `revoke` (soft per OQ5), `mark_used`, `list_keys`, `list_stale`, `get_by_id`. Key shape: `vk_` + 32 base32 random chars (35 total); visible 8-char prefix indexed for O(1) candidate lookup; full key bcrypt-hashed at cost 12. Added `bcrypt>=4.0` to `[project] dependencies`.
- **`voicegateway.storage.tenants_repo`** (REQ-VG-TENANT-002). Read-side derived view over `DISTINCT sessions.tenant_id`: `list_tenants` (substring typeahead with `%`/`_` escaped to literal characters), `get_tenant`, `count_tenants`, `get_unattributed_aggregates`. No separate tenants table; aggregates roll up live from the sessions UPSERT.
- **Auth middleware in `voicegateway/server/main.py::build_app`** detects `vk_`-prefixed bearer tokens, resolves them via `virtual_keys_repo.verify`, bumps `last_used_at`, sets the tenant ContextVar if scoped, and stashes `virtual_key_id` + `virtual_key_tenant_id` on `request.state` for downstream conflict checks. Static keys are unchanged. New `voicegateway.core.auth` helpers: `is_virtual_key_token`, `verify_virtual_key`, `check_tenant_body_conflict` (403 on scoped-key + body-tenant mismatch).
- **`attach_session(..., tenant_id="…")`** kwarg threads the tenant through the v0.2.0 session-attach helper. Setting it pushes onto `tenant_id_ctx` so the first `log_request` for the session picks it up. Omitting it leaves the ContextVar alone (a virtual-key-set scope still wins).
- **`log_request` writes `tenant_id`** to both the `requests` and the `sessions` rows from `current_tenant()`. The sessions UPSERT uses `COALESCE(tenant_id, excluded.tenant_id)` so the first tenant-bearing request stamps the row for its lifetime; later unattributed requests cannot blank it. `turns_repo`, `dead_air_repo`, and `replay_repo` write functions accept an optional `tenant_id` kwarg defaulting to `current_tenant()` for per-batch propagation.
- **`TenantConfig` per-project YAML knob**: `tenant.virtual_key_stale_days` (default 90, `ge=1`). Mirrored across the Pydantic schema and the runtime dataclass.
- **Five new dashboard endpoints**: `GET /api/tenants` (list + unattributed aggregates), `GET /api/tenants/{id}` (single-tenant aggregates, 404 when unseen), `GET /api/virtual_keys` (list, plaintext never appears), `POST /api/virtual_keys` (issue, plaintext ONCE), `POST /api/virtual_keys/{id}/revoke` (soft). Existing handlers `/api/costs`, `/api/latency`, `/api/logs`, `/api/sessions`, `/api/metrics` accept a `tenant` query param (`null` = no filter, `""` = unattributed, value = that tenant).
- **Storage methods extended**: `get_cost_summary`, `get_cost_by_project`, `get_recent_requests`, `list_sessions`, `get_latency_stats` accept a `tenant: str | None = None` kwarg with the same three-state convention. `list_sessions` / `get_session` SELECT `tenant_id` and `_row_to_session` surfaces it on the returned dict.
- **`dashboard/frontend/src/pages/VirtualKeys.tsx`**. Issue / list / revoke flow with the show-key-once modal that puts the plaintext in a yellow card with copy-to-clipboard (REQ-VG-TENANT-003 AC-2). Active / stale / revoked status badges; 90-day default stale threshold.
- **`dashboard/frontend/src/components/{TenantFilter,TenantPill,FilterBar}.tsx`**. `TenantFilter` is a 200ms-debounced typeahead with All-tenants / Unattributed / per-tenant sections. `TenantPill` renders attributed vs muted-unattributed in three modes. `FilterBar` is a shared filter strip with URL sync via `useSearchParams`, plus a `useTenantFilter()` hook.
- **Costs, Sessions, Metrics pages** now consume `FilterBar` and forward the tenant param to their data fetches. Sessions gains a Tenant column with `TenantPill asLink` (one-click rescope) and a TenantPill in the SessionDetail modal header.
- **App nav** registers `/virtual-keys` route and a Virtual Keys sidebar entry between Providers and Settings.
- **Typed fetchers + types** in `dashboard/frontend/src/lib/{api,types}.ts`: `TenantRow`, `UnattributedAggregates`, `TenantsResponse`, `TenantFilter` union, `VirtualKey`, `CreatedVirtualKey`; `fetchTenants`, `fetchTenant`, `fetchVirtualKeys`, `createVirtualKey`, `revokeVirtualKey`, `appendTenantParam` helper, and a `tenant` kwarg on `fetchMetricsSummary`.
- **`voicegw tenant` command group** with `list` and `show <id>` subcommands. Read-only (issuing keys stays a dashboard-only flow per REQ-VG-TENANT-003 AC-2). `--json` flag on both for CI scripts.
- **67 new tests** across `tests/storage/` (migration 0005, virtual_keys_repo, tenants_repo), `tests/server/` (virtual key auth, session-create tenant, tenant filter), `tests/inference/` (three-tenant aggregation). Full suite: 1442 → 1509 (+67). Coverage: 87.58% (above the 80% Foundry gate).
- **`docs/api/python-sdk.md`** grew a "Tenant attribution (v0.4.0)" section after `attach_session`.
- **`docs/guide/multi-tenant-quickstart.md`** is the operator-facing walkthrough: tag at session-create, issue a virtual key, view per-tenant data, export.

### Changed

- **Embedded version strings** bumped from `0.3.0` to `0.4.0` in `voicegateway/server/main.py` (FastAPI ctor + `/health.version`), `dashboard/api/main.py` (FastAPI ctor), `tests/server/test_server.py` (version assertion), `docker/voicegateway.Dockerfile` (`ARG VERSION`), and the dashboard sidebar pill in `dashboard/frontend/src/App.tsx`.

### Decisions locked (Foundry Open Questions)

- **OQ1 virtual key shape** — `vk_` + 32 base32 random chars (35 total). 8-char visible prefix (`vk_` + 5 random) stored to bound the bcrypt candidate set; full key hashed at cost 12.
- **OQ2 tenant identifier** — 128-char UTF-8 max. Spaces and unicode allowed; the tenants_repo typeahead disambiguates confusable names.
- **OQ3 backfill** — NO. Migration 0005 adds `tenant_id` nullable with no DEFAULT; pre-v0.4.0 rows stay NULL forever and the FE renders them as the muted "unattributed" pill.
- **OQ4 conditional ALTER** — `sqlite_master` presence check before each derived-table ALTER. v0.0.5-only deployments run migration 0005 cleanly without erroring on absent `turns` / `dead_air_events` / `replay_*` tables.
- **OQ5 revocation** — Soft. `UPDATE virtual_keys SET revoked_at = CURRENT_TIMESTAMP`. The row persists for audit + stale-key detection; hard delete is out of scope.

### Notes

- **No automatic backfill** of pre-v0.4.0 sessions. They stay `tenant_id = NULL` (the unattributed bucket).
- **No CLI issuance of virtual keys** per REQ-VG-TENANT-003 AC-2.
- **No `voicegw costs --tenant` flag yet** — the dashboard's `/api/costs?tenant=…` is the canonical per-tenant cost source for v0.4.0.
- **No re-tag affordance for already-attributed sessions.** The COALESCE rule fills NULL slots only; a re-tag flow for unattributed sessions is on the v0.4.x roadmap.
- **Virtual keys do not carry RBAC scopes** in v0.4.0 — a verified vk satisfies every scope. RBAC layering follows in a future release.

## v0.3.0 -- 2026-05-11

**Conversation replay and debugging.** The universal "this saved me hours" feature for voice-agent developers. Open any past conversation in the dashboard, scrub through it like a video timeline, and see every STT chunk, every LLM token, every TTS frame, plus the agent's conversation state at every moment — with cost accruing live as you scrub. Replay is captured by default for every session, full fidelity, with per-project `retention_days` (default 90) ageing rows out automatically.

### Added

- **`voicegateway.middleware.replay_capture.ReplayCapture`** (REQ-VG-REPLAY-003). Per-session asyncio buffer that captures STT chunks, LLM tokens, TTS frames, and conversation-state snapshots keyed by `session_id`. Bounded backpressure: at `buffer_size_events` (default 5000), the oldest event is dropped and a `dropped_count` increments — the dashboard surfaces this as "events dropped here" rather than silently misleading. Auto-flushes at `flush_size_events` (default 500) or on session close.
- **`voicegateway.middleware.state_snapshotter.StateSnapshotter`** (REQ-VG-REPLAY-005). Pydantic `StateSnapshot` model serialized at LLM message-add, tool-call-invoke, and tool-call-resolve boundaries. Per-session rate cap of one snapshot per second guards against storage explosion on chatty agents; tool-resolve snapshots bypass the cap because the in-flight → done transition is structurally important.
- **Migration 0004** introduces four replay tables (`replay_stt_events`, `replay_llm_tokens`, `replay_tts_frames`, `replay_state_snapshots`) sharing an `(id, session_id, t_ms, payload, provider, cost_usd, created_at)` shape with `(session_id, t_ms)` composite indexes, plus a `replay_size_bytes` nullable column on the existing `sessions` table. Idempotent. v0.2.0 sessions preserved with NULL on `replay_size_bytes` (REQ-VG-REPLAY-006 graceful-handling).
- **`voicegateway.storage.replay_repo`** (REQ-VG-REPLAY-001, -006). Flat async function module: `bulk_write_events` partitions by modality and runs `executemany` per partition; `read_full_replay` UNION ALL's the four tables ordered by `t_ms`; `delete_replay` cascades a single transaction across all four tables; `aggregate_storage_per_session` sums `length(payload)` for the dashboard storage-usage view.
- **`voicegateway.storage.retention_worker.RetentionWorker`** (REQ-VG-REPLAY-006). Hourly asyncio task; reads each project's `replay.retention_days` and deletes replay rows tied to sessions whose `ended_at` is older than the window. In-flight sessions (`ended_at IS NULL`) are explicitly excluded. Single-process for v0.3.0; multi-replica coordination is deferred.
- **`SQLiteStorage.finalize_session_replay(session_id)`**. Composes `replay_repo.aggregate_storage_per_session` into a single UPDATE to write `replay_size_bytes` to the sessions row. Called by `CostTracker.close_session` alongside the existing v0.2.0 `finalize_session_metrics`; each call is independently guarded so a failure in one does not prevent the other.
- **Four dashboard endpoints** in `dashboard/api/main.py`: `GET /api/sessions/{id}/replay` (full time-ordered event stream — OQ3 pre-fetch resolution), `DELETE /api/sessions/{id}/replay` (cascade), `GET /api/replay/storage` (per-project byte breakdown), `POST /api/projects/{id}/replay/retention` (in-memory retention update with body-validated `int [1, 365]`).
- **`ReplayConfig` per-project YAML knobs**: `replay.enabled` (default true), `replay.retention_days` (default 90, `ge=1`), `replay.buffer_size_events` (default 5000), `replay.flush_size_events` (default 500). Mirrored across the Pydantic schema and the runtime dataclass.
- **Dashboard `Replay` page** (`dashboard/frontend/src/pages/Replay.tsx`) reading `:sessionId` from the URL, pre-fetching the full replay on mount, and rendering a Scrubber + 2×2 pane grid + RunningCostCounter. Seven subcomponents: `Scrubber` (range input + ArrowLeft/Right step to prev/next event + Shift+Arrow for 1s jump + Home/End for call boundaries), `TranscriptPane` (STT with partial-revision opacity), `ModelOutputPane` (LLM with tool-invoke badges), `SynthesisPane` (TTS with red underrun frames), `ConversationStatePane` (system prompt + message history + tool-in-flight), `RunningCostCounter` (per-modality breakdown + top-3 costliest tooltip), `PreV030Banner` (pre-v0.3.0 session fallback with link to session detail).
- **Sidebar nav route** at `/sessions/:sessionId/replay`. The Sessions page table now carries an "Open replay" column with a `<Link>` per row; clicking propagates straight to the Replay page without opening the session-detail modal.
- **Typed fetchers** in `dashboard/frontend/src/lib/api.ts`: `fetchSessionReplay`, `deleteSessionReplay`, `fetchReplayStorage`, `updateReplayRetention`. Four new TS types in `lib/types.ts`: `ReplayEvent`, `ReplayResponse`, `StateSnapshot`, `RetentionWindow`.
- **`voicegw replay <session-id>`** Typer command. Signpost-only for v0.3.0: prints the dashboard URL for the Replay page. Future scope can embed a Textual mini-replay using the v0.1.1 TUI primitives.
- **41 new tests** across `tests/middleware/`, `tests/storage/`, `tests/server/`, and `tests/storage/test_replay_storage_smoke.py`. The smoke is the **Foundry Open Question 1 gate**: a synthetic 60-second conversation (40 STT chunks + 400 LLM tokens + 1200 TTS frames + 60 state snapshots) is captured end-to-end and the on-disk payload sum is asserted < 600 KB. Suite total: 1401 → 1442 (+41). Coverage: 88% (above the 80% Foundry gate).
- **`docs/storage/replay-storage-costs.md`** with per-modality byte tables, the 130-580 KB/min realistic range, worked solo-dev (~$0.21/month S3 at 90-day retention) and agency (~$70/month) examples, and the three per-project tuning knobs.

### Changed

- **`docs/api/python-sdk.md`** grew a new top-level "Conversation replay capture (v0.3.0)" section between "Session correlation" and "Operations: where to go". Covers the defaults, the per-project YAML knobs, how to disable capture for sensitive projects, and the retention worker mechanism.

### Decisions locked (Foundry Open Questions)

- **OQ1 (storage cost target: 30-100 KB/min)** — RESOLVED AFFIRMATIVE. The OQ1 smoke test in `tests/storage/test_replay_storage_smoke.py` confirms the synthetic 60-second conversation lands well below 600 KB. Fallback path remains in place: `replay.enabled: false` per project disables capture without redeploying.
- **OQ2 (state snapshot delta granularity)** — message-add boundary; max one per second. Tool-resolve bypasses the rate cap.
- **OQ3 (dashboard pre-fetch vs streaming)** — pre-fetch full replay on page mount. Bounded by retention + per-minute capture, so the fetch is tractable.
- **OQ4 (short-call capture)** — capture by default; storage cost trivial.
- **OQ5 (TTS per-frame cost)** — distribute per-character cost across frames in time-weighted slices.

### Migration

- v0.2.0 callers need no code change. Migration 0004 runs automatically; v0.2.0 sessions are preserved with NULL on `replay_size_bytes` and surface as "recorded before replay capture existed" in the Replay page (linked back to the session detail page).
- The `voicegateway/combined_server.py` re-export shim that was originally flagged for v0.2.0 removal **stays in place again**; the replay feature release should not bundle a back-compat break. Schedule remains tentatively v0.4.0.

## v0.2.0 -- 2026-05-11

**Voice-conversation cost and quality metrics.** The four-number screenshot the wedge promises: per-minute-of-conversation cost (talk time as denominator, not wall clock), agent response speed p50/p95 (caller stops → agent first audible byte), talk-over rate (frame-level overlap of agent and caller speech), and dead-air event count (silences past a configurable threshold). All four metrics live together on one screen in the new dashboard **Metrics** page with shared filtering (project + 7-day default window). Pre-v0.2.0 sessions render as "not measured" rather than zero. The retention magnet for v0.0.5's adoption gate.

### Added

- **`voicegateway.middleware.turn_tracker.TurnTracker`** (REQ-VG-METRICS-002, REQ-VG-METRICS-003). Records caller and agent speech intervals per session via plugin-level VAD and audio-frame events. Computes `response_speed_ms` per turn at turn boundary. Buffers turns in memory keyed by `session_id`; flushes to `turns_repo` on session close or every `flush_size` turns (default 25, configurable). Handles edge cases explicitly: missed `user_stopped` events infer caller_end from agent_first_frame and null the response speed; agent-never-speaks turns flush a tail row with NULL agent fields on `close_session`.
- **`voicegateway.middleware.dead_air_detector.DeadAirDetector`** (REQ-VG-METRICS-004). One asyncio task per active session polls an injected activity probe at 1-second cadence, emits a `DeadAirEvent` when silence crosses the threshold (default 3.0 seconds, per-project overridable). One event per discrete silence period; flag resets on activity. Repository- and tracker-agnostic via injectable callable + event callback.
- **`voicegateway.storage.turns_repo`** and **`voicegateway.storage.dead_air_repo`** (REQ-VG-METRICS-002, -003, -004). Flat async function modules: `create_turn`/`create_turns_bulk`, `list_turns_by_session`, `aggregate_response_speed` (p50/p95/p99 via `statistics.quantiles`), `count_overlap_turns` (SQL self-join detecting caller_speak_start before previous agent_speak_end). Dead-air analog: `create_event`, `list_events_by_session`, `count_events_by_filter` with session and half-open time-range filters.
- **Migration 0003 (`voicegateway/storage/migrations/0003_turns_and_deadair.py`)**. New `turns` and `dead_air_events` tables with three indexes plus five nullable aggregate columns on the existing `sessions` table (`talk_time_seconds`, `per_minute_cost_usd`, `response_speed_p50/p95_ms`, `talk_over_rate`). Idempotent via `PRAGMA table_info` guard. v0.1.x sessions preserved with NULL on new columns; no backfill.
- **`SQLiteStorage.finalize_session_metrics(session_id)`** (REQ-VG-METRICS-001, -006). Composes the repo aggregations into a single UPDATE that writes the five aggregate columns. NULL when underlying turn data is absent; per-minute cost NULL when talk_time is zero.
- **`voicegateway.inference.attach_session(agent_session)`** (Foundry escape hatch). Opt-in helper that subscribes to `AgentSession` events (`user_started_speaking`, `user_stopped_speaking`, `agent_started_speaking`, `agent_stopped_speaking`, `close`) and wires them into the TurnTracker, DeadAirDetector, and CostTracker via a process-level component registry. Use when custom AgentSession subclasses or in-process harnesses make the standard plugin hooks miss events. Component registry populated by `register_components` (Gateway startup path) or explicit kwargs (test path).
- **Three dashboard endpoints** in `dashboard/api/main.py`: `GET /api/metrics?project=&days=` (aggregated metrics over the filter window), `GET /api/sessions/{id}/turns`, `GET /api/sessions/{id}/dead_air`. Reuse existing dashboard auth and CORS configuration.
- **`MetricsConfig` per-project YAML knobs**: `metrics.dead_air_threshold_seconds` (default 3.0), `metrics.talk_over_min_overlap_ms` (default 100), `metrics.turn_buffer_flush_size` (default 25). Added to both the Pydantic schema (`voicegateway/core/schema.py`) and the runtime dataclass (`voicegateway/core/config.py`).
- **Dashboard `Metrics` page** (`dashboard/frontend/src/pages/Metrics.tsx`) with four cards in a 2×2 grid: `PerMinuteCostCard` (green), `ResponseSpeedChart` (blue), `TalkOverChart` (orange), `DeadAirList` (red). Shared filter row (project + 1/7/30/90 days) updates all four metrics simultaneously. "Not measured" badge replaces zero when aggregates are NULL (REQ-VG-METRICS-006). Navigation entry between Sessions and Projects in the sidebar.
- **Typed fetchers** `fetchMetricsSummary`, `fetchSessionTurns`, `fetchSessionDeadAir` in `dashboard/frontend/src/lib/api.ts`. Three new TS types (`MetricsAggregate`, `TurnRow`, `DeadAirEvent`) in `lib/types.ts`.
- **45 new tests** across `tests/middleware/`, `tests/storage/`, `tests/server/`, and `tests/inference/`. Five new test files plus integration coverage for the `attach_session` pipeline. Total suite: 1356 → 1401 (+45). Coverage: 89% (Foundry 80% gate cleared).

### Changed

- **Dashboard `StalenessBanner` documented placement** expanded from Costs + Overview + Sessions to also include Metrics. Per-page mounting (the existing pattern) means adding the Metrics page is an import + render at the top of `Metrics.tsx`.
- **CHANGELOG mirror behaviour** introduced in v0.1.2's T20 remains: root `CHANGELOG.md` is canonical; the docs site's `docs/reference/changelog.md` is regenerated by `docs/package.json`'s `prebuild`/`predev` hooks. The v0.2.0 entry propagates to the docs site at the next docs build.
- **Migration framework introduced.** `voicegateway/storage/migrations/` is a new subpackage with versioned migration files exporting an async `apply(db)` coroutine. v0.1.x's inline ALTER TABLEs in `_ensure_initialized` stay; future schema work uses the migrations layout.

### Decisions locked (Foundry Open Questions)

- **OQ1 (plugin-level VAD/audio-frame hooks reliable on stock AgentSession?)** — escape hatch `attach_session` is in place; full stock-SDK validation is a release-PR-time manual smoke step (not in the unit-test pipeline because livekit-agents adds a network and audio-codec dependency).
- **OQ2 (talk-over minimum overlap threshold)** — locked at 100ms; per-project overridable.
- **OQ3 (dead-air threshold default)** — locked at 3.0s per Refinery; per-project overridable.
- **OQ4 (precompute vs on-demand session_metrics)** — precompute on session close via `CostTracker.close_session(sid)` calling `SQLiteStorage.finalize_session_metrics(sid)`.
- **OQ5 (`/api/metrics` default time window)** — 7 days, matching the Costs page.

### Migration

- v0.1.x callers do not need any code change. Migration 0003 runs automatically on first `SQLiteStorage.start()`; v0.1.x sessions row entries are preserved with NULL on the new aggregate columns and surface as "not measured" in the Metrics view.
- The `voicegateway/combined_server.py` re-export shim flagged for v0.2.0 removal in the v0.1.2 release notes stays in place — schedule slipped to v0.3.0 to avoid bundling a deprecation removal with the metrics-feature release.

## v0.1.2 -- 2026-05-11

**Project polish.** Housekeeping pass before v0.2.0 metrics work begins. No buyer-facing behavior changes, no new features, no breaking imports. The repo tree resets to a clean baseline: tests live where you expect them, Dockerfiles consolidated under `docker/`, scripts use underscore convention, three top-level modules folded into subpackages with re-export shims, standard root metadata in place, public-API contract via `__all__` declared on every subpackage, code-style conventions documented with linter audits.

### Changed

- **Tests relocated into subdirs** (REQ-VG-POLISH-001). 9 loose root tests moved into the canonical `tests/{cli,integration,providers,middleware}/` subdirs. `tests/integration/` is the new home for cross-cutting tests (`test_integration.py`, `test_reconcile.py`). 7 `Path(__file__).resolve().parent.parent` sites patched to compensate for the deeper file paths.
- **Dockerfiles consolidated under `docker/`** (REQ-VG-POLISH-002). `/Dockerfile` -> `docker/voicegateway.Dockerfile`; `/dashboard/Dockerfile` -> `docker/dashboard.Dockerfile`. `docker-compose.yml`, the docker-publish GitHub Actions workflow, `deploy/fly/{deploy.sh,fly.toml}`, and the documented compose examples updated to reference the new paths.
- **Scripts use underscore convention** (REQ-VG-POLISH-003). `scripts/record-streaming-fixtures.py` -> `scripts/record_streaming_fixtures.py`; `scripts/smoke-v005-inference.py` -> `scripts/smoke_v005_inference.py`. 15 reference sites updated across scripts, tests, and docs.
- **Top-level Python files folded into subpackages** (REQ-VG-POLISH-004). `voicegateway/server.py` -> `voicegateway/server/main.py` (re-export shim at the subpackage `__init__`). `voicegateway/combined_server.py` -> `voicegateway/server/combined.py` (with a temporary back-compat shim at the original path, flagged for removal in v0.2.0). `voicegateway/reconcile.py` -> `voicegateway/reconcile/core.py` (re-export shim at the subpackage `__init__`). Every v0.1.x import path keeps working; a new contract test enforces this on every PR.
- **Code-style documentation expanded** (REQ-VG-POLISH-007). `docs/contributing/code-style.md` now names every existing convention: `typing.Protocol` over ABC for structural typing, Pydantic for config, async throughout, exception-handling-at-boundaries (rather than a blanket "no broad except" rule that the codebase does not actually follow), leading-underscore internal modules, public-API contract via `__all__`, test patterns.
- **Ruff config rationale documented** (REQ-VG-POLISH-007). Each selector and ignore now carries an inline comment explaining its choice. Explicit `[tool.ruff.format]` block declares the format settings that agree with `.editorconfig` defaults.
- **Mypy config tightened** (REQ-VG-POLISH-007). Added `warn_redundant_casts`, `warn_unused_ignores`, `no_implicit_optional`. `warn_unused_ignores` surfaced and removed 8 dead `# type: ignore` comments across `voicegateway/cli/tui/screens/_focus.py`, `voicegateway/middleware/instrumented_provider.py`, `voicegateway/mcp/server.py`, and `voicegateway/cli/smoke_test.py`. Mode remains basic for v0.1.x; per-module strict deferred to v0.2.0+.
- **README references project metadata.** New "Project metadata" section links to CHANGELOG, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, LICENSE, and the `docker/` directory.

### Added

- **Root metadata files** (REQ-VG-POLISH-005). `CHANGELOG.md` is now canonical at repo root; the docs site mirrors it at build time via `docs/package.json`'s `prebuild`/`predev` hooks. `CONTRIBUTING.md` is a one-page contribution flow pointing to `docs/contributing/` for deeper guides. `SECURITY.md` documents the disclosure policy: GitHub Security Advisory preferred, `mahimairaja3@gmail.com` email fallback with `[voicegateway-security]` subject prefix, best-effort 7-day acknowledgment / 14-day triage / 30-day patch for high-severity, latest-minor-only support window while on v0.x. `.editorconfig` sets editor-side defaults (utf-8, LF, 4-space Python, 2-space everything-else, `trim_trailing_whitespace = false` on Markdown to preserve the two-trailing-spaces line-break convention) and agrees with `ruff format`.
- **`__all__` on every subpackage** (REQ-VG-POLISH-006). All 18 `voicegateway/**/__init__.py` files now declare `__all__` -- the v0.1.x public surface is explicit. Subpackages with no top-level re-exports declare `__all__: list[str] = []` so callers know to reach into submodules directly.
- **Contract tests for the public surface.** `tests/integration/test_v0_1_x_imports.py` (12 tests) asserts every v0.1.x import path resolves through the new re-export shims (REQ-VG-POLISH-004 AC-2). `tests/integration/test_public_api.py` (73 parametrized tests across 18 subpackages) walks every subpackage and asserts `__all__` exists, is a list of str, contains no underscore-prefixed names, and every name resolves on the package (REQ-VG-POLISH-006 AC-1).

### Removed

- **`docs/reference/changelog.md` is no longer tracked.** Foundry Open Question 2 resolved (recommended path): root `CHANGELOG.md` is the single source of truth; the docs-site mirror is a derived artifact regenerated by the prebuild hook on every docs build. Existing internal docs links (`/reference/changelog` from `docs/migration/version-upgrades.md`, `docs/reference/troubleshooting.md`, `docs/reference/faq.md`) keep working because the prebuild script runs before VitePress.

### Migration

- The `voicegateway/combined_server.py` re-export shim is temporary scaffolding flagged for removal in v0.2.0. Downstream callers using `from voicegateway.combined_server import build_combined_app` should plan to migrate to `from voicegateway.server.combined import build_combined_app` before v0.2.0.
- No other v0.1.x import paths break. The new contract tests enforce this on every PR.

## v0.1.1 -- 2026-05-10

**Terminal UI fast-follow.** v0.1.1 is the four-tab Textual-based terminal interface the v0.1.0 launch trailer promised: launch with `voicegw tui` for live monitoring of sessions, costs, logs, and providers without leaving the shell. Dual mode -- Gateway (1 s daemon polling) or Local (5 s direct SQLite read, daemon-down friendly) -- plus full vim navigation, a `?` help overlay, brand-orange focus rings, and a reconnection indicator that flips on connection loss and back when the daemon returns. v0.1.0's public API, command surface, and storage layout are preserved verbatim; v0.1.1 is purely additive.

### Added

- **`voicegw tui` command.** Single-command launcher (REQ-VG-TUI-001) opening a four-tab Textual interface. Flags: `--local` (read SQLite directly; bypass daemon regardless of state per locked decision 5), `--url` (daemon URL, defaults to `serve.host`/`serve.port` from `voicegw.yaml` with `0.0.0.0` rewritten to `127.0.0.1`), `--token` (bearer for daemon write paths), `--history-limit` (initial row count for Sessions/Logs, default 100), `--theme` (default `brand`), `--poll` (cadence override; defaults 1.0 s Gateway / 5.0 s Local), `--config` / `-c`. A 2-second `/health` preflight runs before Textual takes over the terminal so an unreachable daemon prints a one-line pointer + exits 1 instead of stalling in the alternate-screen buffer.
- **Sessions tab** (REQ-VG-TUI-002, key `1`). Recent voice conversations with start time, duration, total cost, and providers used per row. `s` toggles the sort column between cost and start time; the active sort is visually indicated. `Enter` on a focused row opens a per-turn drill-in `ModalScreen`. Live append: new conversations appear at the top within 5 s in Gateway mode (polling-based; SSE deferred per locked decision 1).
- **Costs tab** (REQ-VG-TUI-003, key `2`). Today's total as a single dollar number with a per-modality breakdown (STT, LLM, TTS) beneath. `r` cycles the active range between today, this week, and this month. A `(as of X)` freshness suffix appears when the pricing data is from `genai-prices` with a timestamp older than 24 h, matching the web dashboard's behavior.
- **Logs tab** (REQ-VG-TUI-004, key `3`). `RichLog`-based scrolling tail with auto-scroll to bottom (RichLog's default `auto_scroll=True`). `/` opens a substring filter input; `Escape` clears it. Vim scroll motions (`j`/`k`, `g`/`G`) are bound at the screen level so filter-focus does not eat them.
- **Providers tab** (REQ-VG-TUI-005, key `4`). Configured providers with the indicator + colour treatment from the Refinery: `[ok]` (green `$success`), `[fail]` (red `$error`), `[?]` (muted text). Owning project appears next to each provider; global providers show `(global)`. `t` on a focused row runs `test_provider` against the upstream API in a Textual worker (`exclusive=True` so a double-press cancels the in-flight call rather than racing two probes); the indicator flips in place within 3 s.
- **Vim keybindings + `?` help overlay** (REQ-VG-TUI-006). Global: `q` (quit), `?` (help), `1`/`2`/`3`/`4` (jump to tab), `Tab`/`Shift+Tab` (cycle). Row-based screens: `j`/`k` (down/up), `g`/`G` (first/last), `h`/`l` as hidden aliases for muscle memory. Logs: `j`/`k` scroll one line, `g`/`G` top/bottom. The `?` overlay derives the cheatsheet directly from each screen's `BINDINGS` -- no parallel `KEYBINDINGS` constant -- so dropping or adding a binding surfaces in the overlay automatically. `show=False` entries (`h`/`l` aliases) are filtered out so the documented contract stays clean.
- **Live updates + reconnection** (REQ-VG-TUI-007). A live-counter row sits between the `ContentSwitcher` and the Textual `Footer`; polls `list_costs(period='today')` at the active client's cadence and renders `Today: $X.XXXX   Requests: N`. `HttpClient` flips `is_connected=False` on `ConnectError` / `Timeout` / network failure; the counter row then renders `Reconnecting to daemon...` until the next successful round-trip. Exponential backoff is deferred (the polling cadence is the natural retry interval).
- **Local mode** (REQ-VG-TUI-008). `--local` bypasses the daemon regardless of state; `LocalClient` wraps `SQLiteStorage` for read paths and raises `LocalModeUnsupportedError(feature='test_provider')` on write attempts. A persistent `[Local mode]` chip renders in the header on `$warning` background (deliberately different from the active-tab `$accent` so the modes are unmistakable). The counter row appends `(as of N s/min/h/d ago)` from the SQLite file's mtime so the user sees snapshot freshness at a glance. Write attempts surface as warning notifications via Textual's `app.notify` with the daemon-required title -- no silent failures, ever.
- **Brand styling** (`voicegateway/cli/tui/styles/main.tcss`). Brand-orange `#D85A30` focus ring on every focusable widget, an active-tab indicator using a `border-left thick`, modal panels with `border: thick $accent`, plus cross-screen utilities `.tab-header` / `.tui-list` / `.empty-state`. Textual's `Color.downgrade()` handles graceful colour downgrades on 256-colour and 16-colour terminals automatically; no separate downgrade table needed. Refinery's "no decorative borders" rule audited: every surviving border carries state (focus ring, active tab, modal boundary, card identity).
- **`docs/cli/tui.md`** reference page. Sections: launch, the four tabs, full vim keybindings reference, mode comparison table (Gateway vs Local), troubleshooting (terminal too small, 256-colour terminals, Local mode write attempts, daemon unreachable, reconnection), exit codes, related commands.

### Changed

- **Public command surface** grew from 21 commands (v0.1.0) to 22 with the addition of `tui`. The v0.1.0 set is unchanged; the back-compat assertion test continues to pin every prior command, so a future regression that drops a v0.0.5 or v0.1.0 command trips immediately.
- **`pyproject.toml` `tui` extra** (`textual>=0.85,<0.90`). Tight upper bound matches design.md section 8.1 risk mitigation; revisit at v0.1.2. `httpx>=0.27` was already a base dependency from v0.0.5 and is reused by the TUI's `HttpClient`. The `[tui]` extra is also added to the dev extras so Pilot-based screen tests are part of the default development install.
- **`README.md`** Quick Start section adds a `voicegw tui` line to the ad-hoc-operations code block plus a one-paragraph description framing the TUI as live monitoring without leaving the shell. The Installation section gains a per-extra row for `[tui]` and the "Everything" pip-install example now includes it by default.

### Limitations

- **Polling-based live updates.** Server-Sent Events are deferred per locked decision 1; the live-counter and Sessions live-append both poll the daemon at the configured cadence (1 s Gateway, 5 s Local). The cadence is overridable via `--poll`.
- **Exponential backoff on reconnection is deferred.** The polling interval is the natural retry cadence; a future milestone may add a true exponential schedule if it proves valuable in long disconnect windows.
- **`gg` (vim "go to top") ships as single `g`.** The two-key form would require state tracking that is overkill for v0.1.1; `g` jumps to first, `G` jumps to last across every list screen.
- **Manual screenshot pass + multi-terminal smoke** is an operator gate (see the v0.1.0 release pattern). Pilot tests cover the structural rendering; visual polish on a real terminal is mahimairaja's manual check.

### Out of scope (deferred)

- Server-Sent Events for live updates -- deferred per locked decision 1.
- Single-binary distribution and auto-update -- still deferred from v0.1.0.
- v0.2.0 metrics dashboard view -- still paused pending v0.1.0/v0.1.1 adoption signal.

---

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
  - `scripts/record_streaming_fixtures.py` records the six minimum Phase 3 fixtures end-to-end across three providers and two modes each: OpenAI `gpt-4o-mini` LLM batch + stream, Deepgram `nova-3` STT batch + stream, Cartesia `sonic-3` TTS batch + stream. Default-deny gating: no flags prints a recording-disabled banner; `--record` alone prints a per-fixture cost estimate; `--record --confirm` actually hits the API. A `--all` flag runs all six sequentially with a single `--confirm` and a ~$0.013 aggregate estimate.
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
- **`scripts/record_streaming_fixtures.py --all` mutex bypass** (Phase 3 Codex adversarial review). The `--all` branch advertised mutual exclusivity with `--provider`, `--modality`, `--model`, `--mode`, but the runtime check omitted `--mode`; because `--mode` defaulted to a truthy `"batch"`, an explicit `--record --all --mode stream` would silently record all six fixtures and bill the operator outside the apparent command intent. `--mode` now defaults to `None` so it only registers when passed; the `--all` mutex check enumerates all four narrowing flags and `parser.error()`s naming each offender. Three regression tests pin the contract.
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
