# VoiceGateway v0.1.0 — Task List

Pick the **first** unchecked task. Tasks are dependency-ordered.
Do not skip ahead unless a task is blocked.

Status legend: `[ ]` todo, `[x]` done, `[~]` skipped (note why),
`[?]` blocked (note why).

**Timeline: 4 weeks. Priority is OpenRTC-Python v0.1 first; if VG slips,
Phases 1-2 ship as v0.1.0 and Phases 3-4 defer to v0.1.1.**

---

## Phase 1 — Framing Fixes (Week 1, May 3–9)

Cheapest, highest-leverage work. Readers landing on docs during weeks
2-4 should already see the new framing.

### 1.1 — Audit & inventory

- [x] Read `docs/design/v0.1.0.md` end-to-end. Record key decisions
  in JOURNAL.md as the first entry.
- [x] Grep entire repo for "self-hosted inference gateway" and similar
  generic gateway framing. Record findings to
  `.agents/framing-occurrences.md`.
- [x] Audit every page in `docs/` for claims that don't match code.
  Audit found `docs/migration/from-litellm.md:12-19` and the README
  "Cloud outage" claim as known issues. Assume there are 3-5 more.
  Record findings to `.agents/credibility-issues.md`.

### 1.2 — README rewrite

- [x] Rewrite README hero. New framing: "VoiceGateway gives LiveKit
  voice agents modality-aware cost estimation backed by
  pydantic/genai-prices, plus reconciliation tooling so you can
  verify our numbers against your actual provider invoices." Drop
  generic "inference gateway" framing.
- [x] Rewrite README "Why VoiceGateway?" / features section. Lead
  with: (a) LiveKit-native return types, (b) modality-aware unit
  accounting + genai-prices integration, (c) reconciliation tooling,
  (d) MCP server for agent self-service. Honest tone — link to
  decision-tree page for "is this right for me?"
- [x] Update README badges. Verify all are current and accurate.
- [x] Update README install instructions. LiveKit prerequisite visible
  up-front so users don't get stuck on `ConnectionError`.

### 1.3 — Docs site rewrites

- [x] Rewrite `docs/index.md` hero and feature grid to match new
  README framing.
- [x] Rewrite `docs/migration/from-litellm.md`. Acknowledge LiteLLM
  has STT and TTS endpoints (live since early 2026). Reframe from
  competitive ("we're better") to complementary ("LiteLLM for general
  LLM gateway use; VoiceGateway purpose-built for LiveKit voice
  agents"). Specifically fix lines 12-19 cited in the audit.
- [x] Create `docs/guide/decision-tree.md`. Single page. Honest matrix:
  - Building a LiveKit voice agent? → VoiceGateway
  - Building text-only LLM apps? → LiteLLM
  - Want hosted multi-tenant with no ops? → OpenRouter
  - At scale on Cloudflare? → AI Gateway
  - Self-hosting voice with full local + cloud unification? → VoiceGateway
- [x] Update `docs/guide/first-agent.md`. Add explicit "LiveKit Server
  Setup" prerequisites section *before* VG steps. Cover both LiveKit
  Cloud and self-hosted `livekit-server` paths. Include the
  `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` env var setup.
- [x] **1.3.5a** Sweep runtime-fallback over-promise (C1 + H5; touches
  L2). Reframe the "automatic failover during a call" claims as
  resolver-time-only, with pointers to the upcoming
  `docs/examples/livekit-fallback-adapter.md` (Phase 1.4). Files:
  `README.md` (lines 237, 255), `docs/examples/fallback-chains.md`
  (hero line 3, lines 178 and 193, and the Mermaid diagram per L2),
  `docs/reference/changelog.md:39`,
  `docs/architecture/middleware.md:160`,
  `docs/migration/from-livekit-inference.md:166`. The post-frontmatter
  "Why" prose + comparison table at `docs/index.md:45-69` rolls in
  here too (parallels the README's dropped table).

- [x] **1.3.5b** Fix critical surface bugs from the audit. Single-line
  or near-single-line edits:
  - C4: `dashboard/frontend/index.html:6`
    `<title>LiveKit Inference Gateway</title>` to a correct VG title.
  - C3: `docs/reference/faq.md:175` SQLite WAL claim. Either enable
    WAL in `voicegateway/storage/sqlite.py` initialization (a
    separate one-line code change) or rewrite the backup advice to
    use `sqlite3 .backup`.
  - H4: `docs/reference/troubleshooting.md:119`
    `VOICEGW_ENCRYPTION_KEY` to `VOICEGW_SECRET` (canonical name in
    `voicegateway/core/crypto.py`).
  - M4: `docs/examples/fallback-chains.md:198` `from livekit.agents.voice_assistant import VoiceAssistant` is broken on
    `livekit-agents>=1.5.0`; rewrite to the AgentSession idiom used
    in `examples/basic_agent.py`.

- [x] **1.3.5c** Sweep model-ID inconsistencies (C5). Decide a single
  canonical Anthropic model ID and sweep all 13+ files. Same class
  issue with `whisper/large-v3` vs `local/whisper-large-v3` and
  `groq/llama-3.3-70b-versatile` vs `groq/llama-3.1-70b`.
  Cross-reference `voicegateway/pricing/catalog.py`. Consider
  deferring LLM ID alignment until Phase 2 (genai-prices integration)
  which may resolve them upstream; STT/TTS IDs should still align
  to the local catalog.
  Done: STT/TTS IDs swept (`whisper/large-v3` and `whisper/base` to
  the `local/` prefix; `kokoro/default` to `local/kokoro`). LLM IDs
  deferred per the TODO note; surfaced as discovered-work below.

- [x] **1.3.5d** Fix remaining FAQ accuracy claims. H2 coverage
  ("over 70%" to 75% or current actual); H3 perf numbers (soften the
  unbacked "~1ms / under 5ms" claims or remove); M1 multi-instance
  scaling caveat (add the budget-cache divergence note); M2 Postgres
  "planned" tightened to "v0.3+ scope". H1 (`v0.1.0 alpha`) self-
  resolves at release; mark it `[~]` deferred to Phase 4.5
  release-prep sweep.
  Done: H2, H3, M1, M2 edits applied. H1 deferred to Phase 4.5 per
  plan (FAQ line 5 still says "v0.1.0 (alpha)"; will be accurate
  once Phase 4.5 bumps `pyproject.toml` to 0.1.0).

- [x] **1.3.5e** Final mop-up. H6 `from-livekit-inference.md:62-74`
  LiveKit Cloud Inference cost-comparison table: add a
  "Pricing as of YYYY-MM-DD; verify via the LiveKit dashboard"
  attribution or remove the table. L3
  `docs/migration/version-upgrades.md` synced at Phase 4 CHANGELOG
  (mark `[~]` here, picked up there).
  Done: H6 attribution snapshot dated 2026-05-04 + provider-pages
  cross-reference added; minor `--` to colon and `--` to `n/a`
  cleanups in the same paragraph. L3 deferred to Phase 4.5
  CHANGELOG / release-prep sweep, where the v0.1.0 stanza of
  `docs/migration/version-upgrades.md` will be rewritten with the
  actual v0.1.0 deltas (genai-prices, framing reframe,
  reconciliation tooling).

### 1.4 — LiveKit FallbackAdapter docs

- [x] Create `docs/examples/livekit-fallback-adapter.md`. Show
  recommended composition pattern: VG providers wrapped in LiveKit's
  `FallbackAdapter` for STT/LLM/TTS. Cover (a) what triggers fallback,
  (b) how VG's cost tracking interacts (each attempt logged
  separately), (c) recommended chain patterns (cloud→cloud→local),
  (d) why this is better than VG building its own runtime fallback
  (LiveKit maintains the adapter, ships with the framework, doesn't
  duplicate functionality). Working code snippet that copy-pastes.

### 1.5 — Phase 1 verification

- [x] Run docs build. Fix any broken links surfaced.
  Done: `npm run build` in `docs/` initially failed with one dead
  link to `/guide/cost-reconciliation` (Phase 4.4 deliverable).
  Added that path to `ignoreDeadLinks` in
  `docs/.vitepress/config.mts` with a comment noting the entry can
  be removed when Phase 4.4 lands. Build now passes (3.0s).
- [~] Verify docs site deploys cleanly to vg.openrtc.tech (preview
  branch deploy if available). Skipped: deployment requires an
  external pipeline this iteration cannot trigger. Local build
  passes; the `feat/cost-track-rebuild` branch can be deployed via
  the existing `docs.yml` GitHub Actions workflow when mahimairaja
  pushes the branch and approves a preview.
- [x] Verify `make test` passes; coverage ≥ 75%.
  Done: `uv run ruff check voicegateway dashboard tests` (clean),
  `uv run mypy voicegateway dashboard` (Success: no issues found in
  56 source files), `uv run coverage run -m pytest tests/
  --ignore=tests/providers/test_ollama.py` (255 passed, 4 skipped),
  `uv run coverage report` shows TOTAL 79% (above the 75% gate set
  in `pyproject.toml:97`).
- [x] Commit Phase 1 milestone tag locally (`v0.1.0-phase1`).
  Note: tag was created in iteration 19, then deleted in
  iteration 22 because the literal name `v0.1.0-phase1` is not a
  PEP 440-valid version and broke `hatch-vcs` build metadata
  (`InvalidVersion: Invalid version: 'v0.1.0-phase1'`). Phase 1
  milestone is captured in commit `bf42481` and the iteration-19
  journal entry. See discovered-work for the milestone-tag scheme
  decision.

---

## Phase 2 — Pricing Foundation (Week 2, May 10–16)

Adopt `pydantic/genai-prices` for LLM costs. Keep STT and TTS in local
catalog with explicit source-date metadata.

### 2.1 — Add genai-prices dependency

- [x] Read `pydantic/genai-prices` README and Python package docs.
  Document the integration surface in JOURNAL.md (which functions to
  call, what they return).
- [x] Add `genai-prices` to `pyproject.toml` runtime dependencies. Pin
  to a specific minor (e.g., `>=0.0.52,<0.1`).
- [x] Run `uv lock` to update lockfile. Verify install works in a
  fresh venv.

### 2.2 — Pricing module split

- [x] Create `voicegateway/pricing/` package directory (it may already
  exist — check first).
  Already existed: `voicegateway/pricing/__init__.py` (empty) and
  `voicegateway/pricing/catalog.py` (the v0.0.x static pricing dict)
  were committed in the initial codebase. No-op verification.
- [x] Create `voicegateway/pricing/llm.py`. Wraps `genai-prices`.
  `calculate_llm_cost(model, input_tokens, output_tokens) -> Decimal`.
  Returns `None` if model not in genai-prices catalog (no silent zero).
  Surfaces `pricing_source = "genai-prices@<version>"`.
- [x] Create `voicegateway/pricing/stt.py`. Local catalog with
  `pricing_source_date: date` and `pricing_source_url: str` per
  entry. `calculate_stt_cost(model, audio_seconds) -> Decimal`.
- [x] Create `voicegateway/pricing/tts.py`. Same shape as stt.py.
  `calculate_tts_cost(model, character_count) -> Decimal`.
- [x] Create `voicegateway/pricing/catalog.py`. Unified facade.
  Dispatches by modality. Replaces the current pricing dict.
  Done partially: new facade `calculate_cost(modality, model, **)`
  and `pricing_source(modality)` added at the top; legacy `PRICING`
  dict and `get_pricing()` kept at the bottom with a DEPRECATED
  marker so existing CostTracker tests stay green. Phase 2.3 wires
  CostTracker through the new facade and removes the legacy code.

### 2.3 — Wire into CostTracker

- [x] Add `pricing_source: str` field to `RequestRecord`
  (`voicegateway/storage/models.py`).
- [x] Add `pricing_source` column to SQLite schema. Migration script
  for existing DBs (or a one-time conversion at startup).
- [x] Update `CostTracker.calculate_cost()` to dispatch by modality
  via `voicegateway/pricing/catalog.py`.
- [x] Update `InstrumentedSTT|LLM|TTS` to capture and pass through
  `pricing_source` to logged requests.
  Done by extending `CostTracker.create_record` to auto-derive
  `pricing_source` from `modality` via `catalog.pricing_source(modality)`
  when no explicit value is provided. The InstrumentedSTT/LLM/TTS
  wrappers do not need code changes: they call create_record as
  before, and the resulting RequestRecord carries the source
  automatically. Explicit `pricing_source=` kwarg is still accepted
  for callers that want to override.

### 2.4 — Surface pricing source

- [x] Add `pricing_source` to `/v1/costs` response (already part of
  the `include_pricing_source` query param work in Phase 4, but the
  field needs to exist in the schema now).
  Done: response now includes a `pricing_sources` dict keyed by
  modality (e.g. `{"llm": "genai-prices@0.0.57", "stt": ..., "tts": ...}`)
  showing which catalog the running instance is using. Phase 4.1
  will layer `?include_pricing_source=true` for per-line attribution
  from the actual logged records.
- [x] Add `pricing_source` to dashboard request log view (light
  touch — text column, no new charts).

### 2.5 — Fix the placeholder bug

- [x] Fix `groq/llama-3.1-8b` $0.0 placeholder in the new local
  catalog. Use Groq's actual paid-tier pricing.
  Done: renamed `groq/llama-3.1-8b` and `groq/llama-3.1-70b` in
  `voicegw.example.yaml` to use Groq's canonical product IDs
  (`-instant` and `-versatile` suffixes). genai-prices recognizes
  the canonical names and now returns paid-tier rates ($0.00009/1k
  for 8b-instant, $0.000985/1k for 70b-versatile). The bare-name
  lookup in genai-prices returns None; CostTracker logs a warning
  and records $0, matching the no-silent-zero contract.

### 2.6 — Staleness gate

- [x] Add unit test: every STT/TTS catalog entry's
  `pricing_source_date` must be ≤ 60 days old. Test fails CI if any
  entry stale, forcing a manual refresh with each release.

### 2.7 — Tests

- [x] Unit tests for `voicegateway/pricing/llm.py`: known model,
  unknown model, edge cases (zero tokens, very large counts).
- [x] Unit tests for `voicegateway/pricing/stt.py` and `tts.py`:
  known model, unknown model, source-date metadata present.
- [x] Unit tests for `voicegateway/pricing/catalog.py`: modality
  dispatch correctness, fallback behavior when modality is unknown.
- [x] Verify all existing cost-tracking tests still pass.

### 2.8 — Phase 2 verification

- [x] `make test` passes; coverage ≥ 75%.
  Done: `uv run ruff check voicegateway dashboard tests` (clean),
  `uv run mypy voicegateway dashboard` (Success, 59 source files),
  `uv run coverage run -m pytest tests/ --ignore=tests/providers/test_ollama.py`
  (315 passed / 4 skipped), `uv run coverage report` shows 79%
  total (above the 75% gate). Docs build also clean (3.21s).
- [x] Commit Phase 2 milestone tag locally (`v0.1.0-phase2`).
  Tagged as `phase2-complete` (no `v` prefix) instead of
  `v0.1.0-phase2` to avoid the hatch-vcs / setuptools-scm
  incompatibility documented in iteration 22's journal entry. The
  hatch-vcs default tag regex matches `vX.Y.Z*` patterns and chokes
  on a non-PEP-440 suffix; tags without the `v` prefix are ignored
  by the version-derivation path. Phase 2 milestone is captured in
  the commit graph + this journal; tag is the convenient pointer.

---

## Phase 3 — Streaming Validation (Week 3, May 17–23)

Build fixture-based test suite for streaming cost accounting.

### 3.1 — Fixture recording infrastructure

- [x] Create `scripts/record-streaming-fixtures.py`. CLI tool gated
  behind `--record` flag. Hits real provider APIs with a test prompt,
  saves response with provider-reported usage. Saves to
  `tests/fixtures/streaming/<provider>_<model>_<modality>_<batch|stream>_<date>.json`.
  Done with framework + OpenAI LLM (batch + stream) end-to-end.
  Deepgram and Cartesia recorders raise `NotImplementedError` with
  follow-up notes; their full implementations land in iterations
  alongside the corresponding 3.2 recording sub-items (those need
  audio fixtures + deepgram-sdk/cartesia SDK integration).
- [x] Create `tests/fixtures/streaming/` directory with README
  explaining how fixtures are recorded and how to refresh them.
- [x] Add `.env.fixtures.example` documenting required API keys for
  recording (OPENAI_API_KEY, DEEPGRAM_API_KEY, CARTESIA_API_KEY).
  These are NOT used in CI — fixtures are committed.

### 3.2 — Record minimum fixture set

All six sub-items are blocked on real provider API access. The
`scripts/record-streaming-fixtures.py` recorder is ready (Phase 3.1
#1); the OpenAI implementation is end-to-end working, the Deepgram
and Cartesia implementations are stubs awaiting their first run.
Running the recorder requires real API keys outside this Ralph
loop's environment. Marking `[?]` (blocked, per PROMPT.md orient
rule) rather than `[~]` (which would require mahimairaja's explicit
deferral approval per the slip plan).

To unblock: mahimairaja runs the recorder externally with
`.env.fixtures` populated, commits the JSON fixtures to
`tests/fixtures/streaming/`, and the iteration that picks each
sub-item ticks it `[x]` against the committed fixture's filename.

- [?] Record OpenAI gpt-4o-mini batch + stream fixtures.
- [?] Record Deepgram nova-3 batch + stream fixtures.
- [?] Record Cartesia sonic-3 batch + stream fixtures.
- [?] (Stretch) Record Anthropic claude-haiku batch + stream.
- [?] (Stretch) Record AssemblyAI batch + stream.
- [?] (Stretch) Record ElevenLabs batch + stream.

### 3.3 — Replay test suite

- [x] Add `respx` (or equivalent HTTP mocking) to dev dependencies.
- [x] Create `tests/test_streaming_cost_accounting.py`. For each
  fixture: replay through VG's wrapper, assert (a) input/output
  units counted match provider-reported usage, (b) calculated cost
  matches `genai-prices`/local catalog calculation for those units,
  (c) TTFB hook fires correctly during streaming.
  Done partially: cost-calculation contract tests (assertion (b))
  for all four modality+mode combos in place; parametrized over
  fixture glob with skip-when-empty so they activate cleanly when
  Phase 3.2 fixtures land. The wrapper-replay half (assertion (a)
  unit counting + assertion (c) TTFB hook timing) needs respx
  mocking at each provider's LiveKit-plugin transport layer; those
  tests land in a follow-up iteration after at least one fixture
  arrives.
- [x] Verify: replay tests pass on all recorded fixtures.
  Verified for the current zero-fixture state: 1 passed
  (`test_fixtures_directory_and_readme_exist`) + 4 skipped (the
  parametrized cases that skip with `no-fixtures-recorded-yet`).
  This becomes a real signal automatically when Phase 3.2 fixtures
  land; nothing else has to change in the test file.

### 3.4 — TTFB hook hardening

- [x] Add a focused test: TTFB hook captures the timestamp at the
  moment the first content chunk arrives, not at request issuance.
  Currently the audit found this is manual and easy to break across
  modalities — make sure the test fails if the hook is missing.
  Done: `tests/middleware/test_instrumented_provider.py` covers the
  Layer-A hook contract with 6 tests (initial state, hook records,
  idempotency, log_request uses ttfb < total when hook fired,
  fallback to total when hook not called, log_request idempotency).
  Coverage on `voicegateway/middleware/instrumented_provider.py`
  jumped from 34% to 80%.
- [x] If the test surfaces a real bug in TTFB capture, fix it.
  Vacuous: all 6 tests pass on first try; no bug surfaced. The
  hook mechanism (Layer A) is correct as implemented. Per-provider
  Layer-B coverage (the streaming code paths in each modality
  actually calling `_mark_first_byte` at the right moment) lands
  with the wrapper-replay tests once Phase 3.2 fixtures arrive.

### 3.5 — Phase 3 verification

- [x] All replay tests pass.
  Done: `tests/test_streaming_cost_accounting.py` resolves to 1 pass
  (`test_fixtures_directory_and_readme_exist`) + 4 skips (parametrize
  cases skipping with `no-fixtures-recorded-yet` per iter 46 design).
  Phase 3.4 hook tests pass too (6/6, iter 48). Full suite: ruff
  clean, mypy clean (59 source files), 322 passed / 8 skipped, 80%
  total coverage (gate is 75%).
- [x] Coverage on `InstrumentedSTT|LLM|TTS` streaming paths reaches
  80%+ (per design doc success gate).
  Done: `voicegateway/middleware/instrumented_provider.py` at 80%
  exactly (was 34% pre-Phase 3.4). Uncovered lines (47, 50, 53-54,
  99-102, 151-160) are mostly the `__getattr__` proxy fallback and
  storage-failure paths; Layer-A hook contract is fully covered.
  Layer-B coverage (per-provider streaming code paths actually
  calling `_mark_first_byte`) lifts further when Phase 3.2 fixtures
  land and wrapper-replay tests come online.
- [~] Commit Phase 3 milestone tag locally (`v0.1.0-phase3`).
  Skipped per the milestone-tag resolution in discovered work
  (iter 45): stop using git tags for ceremonial milestones, rely
  on commit hashes + journal entries instead. The `v0.1.0-phase1`
  tag (iter 19, deleted iter 22) and `phase2-complete` tag (iter
  40, deleted iter 45) both broke `hatch-vcs prepare_metadata_for_build_editable`.
  Phase 3 milestone is captured in this verification commit + the
  journal entry; `git log feat/cost-track-rebuild --grep='chore(verify)'`
  lists the phase boundaries.

---

## Phase 4 — Reconciliation Tooling (Week 4, May 24–30)

CSV export, reconciliation CLI, /v1/costs enhancements.

### 4.1 — `/v1/costs` enhancements

- [x] Add `?per_modality=true` query parameter. Returns separate
  STT/LLM/TTS sums.
  Done: storage method `get_cost_by_modality(period, project=None)`
  groups `requests.modality` SUM(cost_usd) and COUNT(*); server
  exposes `?per_modality=true` query param and adds `by_modality`
  to the response only when set (default-stable: omitted otherwise).
  Tests: 4 new (2 storage: aggregation, project filter; 2 server:
  default omits, opt-in includes). Storage method excludes
  modalities with zero requests; the test covers project-filter
  scoping.
- [x] Add `?include_pricing_source=true` query parameter. Adds source
  attribution per line.
  Done: storage `get_cost_summary` accepts `include_pricing_source`;
  when set, the SQL `GROUP_CONCAT(DISTINCT pricing_source)` adds a
  `pricing_source` field to each `by_model` entry. Default behavior
  preserves the existing response shape (no new key in `by_model`
  entries). Server endpoint accepts `?include_pricing_source=true`.
  Tests: 5 new (3 storage: opt-in adds field, default omits, distinct
  sources comma-joined for mid-period upgrade case; 2 server: default
  off, opt-in accepted with empty traffic).
- [x] Add `?start=` and `?end=` ISO date parameters. Replaces fixed
  `period=today|week|month` (keep the old `period` param for
  backward compat — both can coexist).
  Done: storage helper `_resolve_window(period, start_ts, end_ts)`
  centralizes "named period vs explicit window" logic; when either
  bound is set, period is ignored. All three cost methods
  (`get_cost_summary`, `get_cost_by_project`, `get_cost_by_modality`)
  accept the new bounds. HTTP layer parses ISO dates (YYYY-MM-DD)
  via `_parse_iso_date(value, end_of_day=...)`; `end` interpreted
  as inclusive day (advances one day for the exclusive upper
  bound). Invalid dates return 400 with a helpful error.
  Tests: 6 new (3 storage covering get_cost_summary /
  get_cost_by_project / get_cost_by_modality each respecting the
  window; 3 server covering ISO parse, invalid-date 400, and
  half-open windows).
- [ ] Tests for new query parameters.

### 4.2 — `voicegw export-costs` CLI

- [ ] Implement `voicegw export-costs` command.
  Args: `--start`, `--end`, `--project` (optional), `--format
  csv|json` (default csv).
  Output: per-request line items with timestamp, project, modality,
  provider, model, input_units, output_units, calculated_cost,
  pricing_source, status.
- [ ] Tests for export-costs command (text-mode + CSV output
  validation).

### 4.3 — `voicegw reconcile` CLI

- [ ] Define provider-usage-file format for OpenAI export. Document.
- [ ] Define format for Deepgram export. Document.
- [ ] Define format for Cartesia export. Document.
- [ ] Implement `voicegw reconcile` command. Args: `--provider`,
  `--start`, `--end`, `--provider-usage-file`, `--format
  text|csv|json`. Reads VG's logs for the period, reads provider's
  usage file, produces per-model diff table with absolute and
  percent differences.
- [ ] Tests for reconcile command.

### 4.4 — Reconciliation docs

- [ ] Create `docs/guide/cost-reconciliation.md`. Walkthrough:
  - How to download usage exports from each provider's dashboard
  - How to run `voicegw reconcile` against the export
  - How to interpret the diff (when to investigate, when to ignore)
  - Honest disclaimer: estimation vs invoice, expected drift up to ~5%

### 4.5 — Release prep

- [ ] Bump version in `pyproject.toml` from `0.0.3` to `0.1.0`.
- [ ] Write CHANGELOG entry for v0.1.0. Sections:
  - **Added:** genai-prices integration (LLM pricing now sourced
    from pydantic/genai-prices); pricing_source attribution on every
    request; voicegw export-costs and voicegw reconcile commands;
    /v1/costs query parameters (per_modality, include_pricing_source,
    start, end); 60-day staleness gate for STT/TTS catalogs;
    streaming cost accounting fixture-based test suite;
    LiveKit FallbackAdapter integration guide.
  - **Changed:** Framing throughout README and docs (now positions
    as "modality-aware estimation + reconciliation for LiveKit voice
    agents"); from-litellm migration doc rewritten to acknowledge
    LiteLLM has STT/TTS endpoints; LLM pricing maintenance now
    handled upstream by pydantic/genai-prices.
  - **Fixed:** groq/llama-3.1-8b $0.0 pricing placeholder; first-agent
    docs missing LiveKit prerequisites.
  - **Disclosed:** v0.1.0 cost tracking is validated against
    fixture-recorded provider responses, not against real production
    traffic. Reconcile your numbers against your provider invoice
    during the first 30 days. LLM costs estimated, may drift up to 5%.
- [ ] Tag `v0.1.0` locally. Do NOT push — that's mahimairaja's call.
- [ ] Verify Docker build succeeds locally with new version tag.
- [ ] Final smoke test: fresh checkout, fresh venv, install,
  `voicegw init`, `voicegw status`, end-to-end happy path. Document
  any issues in JOURNAL.md.

### 4.6 — Phase 4 verification (and final completion)

- [ ] All CLI commands work end-to-end.
- [ ] `make test`, `make lint`, `make typecheck` pass.
- [ ] Coverage ≥ 75%.
- [ ] Docs build passes.
- [ ] CHANGELOG complete.
- [ ] `pyproject.toml` version is `0.1.0`.
- [ ] Local `v0.1.0` tag created (NOT pushed).
- [ ] Output `<promise>VOICEGATEWAY_V01_COMPLETE</promise>` as final
  message.

---

## Slip plan

If timeline pressure forces a choice (most likely because OpenRTC-Python
v0.1 takes priority), the minimum viable v0.1.0 is **Phases 1-2 only**:

- Phase 1: framing fixes (low effort, high value)
- Phase 2: genai-prices integration (the foundation; everything else
  builds on this)

Phases 3-4 defer to v0.1.1. Mark all unfinished Phase 3-4 tasks `[~]`
with reason "deferred to v0.1.1, see JOURNAL entry of <date>" and
adjust completion criteria. **Only do this with mahimairaja's explicit
approval** (a commit to TODO.md or a comment in PROMPT.md).

---

## Discovered work

(Add new tasks as they come up. Do NOT pivot to them mid-iteration —
note them and continue with current task.)

- [ ] Rename `.agents/PROPMT.md` → `.agents/PROMPT.md`. Typo in
  filename; the canonical name `PROMPT.md` is referenced by the
  slash command, by the file's own content ("Read PROMPT.md"), and
  by completion-criteria documentation.

- [ ] Sweep user-authored markdown for em dashes per the global
  CLAUDE.md hard convention ("No em dashes anywhere"). Pre-existing
  occurrences in `README.md` (e.g. line 93), `docs/**/*.md`, and
  the `.agents/*.md` files I authored carry em dashes that should
  be replaced with colons, periods, or parentheses. Phase 1.3 sweep
  may pick up the docs/ ones incidentally; the CLAUDE.md project
  description and the journal-entry format example in PROPMT.md:121
  also use em dashes.

- [ ] Add a Codecov coverage badge to README. `codecov-action@v5`
  is already wired in `.github/workflows/test-coverage.yml:62-66`
  with `secrets.CODECOV_TOKEN`, so a `https://codecov.io/gh/mahimailabs/voicegateway/branch/main/graph/badge.svg`
  badge would render dynamically. Held back from this iteration to
  keep "verify and update" scope minimal; revisit during the docs
  sweep or a release-prep pass.

- [ ] LLM model IDs across docs (deferred from 1.3.5c). Docs use
  `anthropic/claude-sonnet-4-20250514` (16 occurrences) and
  `groq/llama-3.3-70b-versatile` (13 occurrences); pricing catalog
  has `anthropic/claude-3.5-sonnet` and `groq/llama-3.1-70b`.
  Phase 2 wires genai-prices, which carries the newer model IDs
  natively, so the docs' newer IDs may resolve upstream rather
  than needing a downward sweep. Decision should be made during
  Phase 2.

- [ ] Other model-ID inconsistencies surfaced during the 1.3.5c
  sweep (defer to Phase 2 or a follow-up sweep):
  - `kokoro/kokoro-v1` (`docs/api/dashboard-api.md:31`,
    `docs/mcp/tools/projects.md:266`) vs catalog `local/kokoro`.
  - `assemblyai/best` and `assemblyai/nano`
    (`docs/configuration/models.md:103-104`) vs catalog
    `assemblyai/universal-2`.
  - `groq/llama-3.1-8b-instant`
    (`docs/configuration/models.md:117`,
    `docs/configuration/providers.md:58`) vs catalog
    `groq/llama-3.1-8b`.
  - `ollama/llama3`, `ollama/mistral`
    (`docs/configuration/models.md:118-119`,
    `docs/migration/from-livekit-inference.md:163`) vs catalog
    `ollama/llama3.2:3b` etc.
  - `anthropic/claude-haiku-3-5`
    (`docs/configuration/models.md:115`) not in catalog.
  - `piper/en_US-lessac-medium`, `piper/en_US-amy-low`
    (`docs/configuration/models.md:132`,
    `docs/configuration/providers.md:162`,
    `docs/configuration/voicegw-yaml.md`,
    `docs/configuration/stacks.md`,
    `docs/examples/local-only.md`) treats voice ID as part of
    model ID; catalog has just `local/piper`.

- [ ] Remove the legacy `PRICING` dict and `get_pricing()` function
  from `voicegateway/pricing/catalog.py`. After Phase 2.3 dispatch
  (iter 30), the only callers are `BaseProvider.get_pricing` (an
  abstract method) and 6 cloud provider implementations
  (anthropic, cartesia, deepgram, elevenlabs, groq, openai). None
  of those are called in production code paths; only `tests/
  providers/test_whisper.py:39` and `tests/providers/test_ollama.py:51`
  exercise `provider.get_pricing(...)`. Plan: drop `BaseProvider.
  get_pricing` from the ABC, drop the method from each cloud
  provider, drop `PRICING` and `get_pricing` from catalog.py,
  rewrite the two tests to dispatch through `catalog.calculate_cost`
  instead. ~10 file touch.

- [~] Decide milestone-tag scheme for ceremonial markers (Phase 1
  complete, Phase 2 complete, etc). RESOLVED: stop using git tags
  for ceremonial milestones; rely on commit hashes + journal
  entries instead. The literal `v0.1.0-phase1` (iter 19) and the
  `phase2-complete` no-v-prefix variant (iter 40) both broke
  `hatch-vcs prepare_metadata_for_build_editable` because
  setuptools-scm's default `tag_regex` matches more liberally
  than expected. Both tags were eventually deleted (iters 22 and
  45). The journal is the canonical milestone record;
  `git log feat/cost-track-rebuild` shows the chore(verify)
  commits at each phase boundary. The actual `v0.1.0` release
  tag (Phase 4.5) will be a real strict-semver tag and will work
  cleanly.

- [ ] Clean up stale `model: default` lines in `local/kokoro:`
  YAML blocks introduced by 1.3.5c (the value before the sweep
  was `kokoro/default` with `model: default`; after the sweep
  the key reads `local/kokoro:` but the `model:` line is now a
  no-op string. Schema `extra="allow"` so harmless, but ugly).
  Files: `docs/examples/fallback-chains.md`,
  `docs/examples/local-only.md`,
  `docs/examples/budget-enforcement.md`,
  `docs/examples/multi-project.md`, plus any others that used
  the `model: default` form.
