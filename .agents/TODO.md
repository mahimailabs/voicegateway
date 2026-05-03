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

- [ ] **1.3.5c** Sweep model-ID inconsistencies (C5). Decide a single
  canonical Anthropic model ID and sweep all 13+ files. Same class
  issue with `whisper/large-v3` vs `local/whisper-large-v3` and
  `groq/llama-3.3-70b-versatile` vs `groq/llama-3.1-70b`.
  Cross-reference `voicegateway/pricing/catalog.py`. Consider
  deferring LLM ID alignment until Phase 2 (genai-prices integration)
  which may resolve them upstream; STT/TTS IDs should still align
  to the local catalog.

- [ ] **1.3.5d** Fix remaining FAQ accuracy claims. H2 coverage
  ("over 70%" to 75% or current actual); H3 perf numbers (soften the
  unbacked "~1ms / under 5ms" claims or remove); M1 multi-instance
  scaling caveat (add the budget-cache divergence note); M2 Postgres
  "planned" tightened to "v0.3+ scope". H1 (`v0.1.0 alpha`) self-
  resolves at release; mark it `[~]` deferred to Phase 4.5
  release-prep sweep.

- [ ] **1.3.5e** Final mop-up. H6 `from-livekit-inference.md:62-74`
  LiveKit Cloud Inference cost-comparison table: add a
  "Pricing as of YYYY-MM-DD; verify via the LiveKit dashboard"
  attribution or remove the table. L3
  `docs/migration/version-upgrades.md` synced at Phase 4 CHANGELOG
  (mark `[~]` here, picked up there).

### 1.4 — LiveKit FallbackAdapter docs

- [ ] Create `docs/examples/livekit-fallback-adapter.md`. Show
  recommended composition pattern: VG providers wrapped in LiveKit's
  `FallbackAdapter` for STT/LLM/TTS. Cover (a) what triggers fallback,
  (b) how VG's cost tracking interacts (each attempt logged
  separately), (c) recommended chain patterns (cloud→cloud→local),
  (d) why this is better than VG building its own runtime fallback
  (LiveKit maintains the adapter, ships with the framework, doesn't
  duplicate functionality). Working code snippet that copy-pastes.

### 1.5 — Phase 1 verification

- [ ] Run docs build. Fix any broken links surfaced.
- [ ] Verify docs site deploys cleanly to vg.openrtc.tech (preview
  branch deploy if available).
- [ ] Verify `make test` passes; coverage ≥ 75%.
- [ ] Commit Phase 1 milestone tag locally (`v0.1.0-phase1`).

---

## Phase 2 — Pricing Foundation (Week 2, May 10–16)

Adopt `pydantic/genai-prices` for LLM costs. Keep STT and TTS in local
catalog with explicit source-date metadata.

### 2.1 — Add genai-prices dependency

- [ ] Read `pydantic/genai-prices` README and Python package docs.
  Document the integration surface in JOURNAL.md (which functions to
  call, what they return).
- [ ] Add `genai-prices` to `pyproject.toml` runtime dependencies. Pin
  to a specific minor (e.g., `>=0.0.52,<0.1`).
- [ ] Run `uv lock` to update lockfile. Verify install works in a
  fresh venv.

### 2.2 — Pricing module split

- [ ] Create `voicegateway/pricing/` package directory (it may already
  exist — check first).
- [ ] Create `voicegateway/pricing/llm.py`. Wraps `genai-prices`.
  `calculate_llm_cost(model, input_tokens, output_tokens) -> Decimal`.
  Returns `None` if model not in genai-prices catalog (no silent zero).
  Surfaces `pricing_source = "genai-prices@<version>"`.
- [ ] Create `voicegateway/pricing/stt.py`. Local catalog with
  `pricing_source_date: date` and `pricing_source_url: str` per
  entry. `calculate_stt_cost(model, audio_seconds) -> Decimal`.
- [ ] Create `voicegateway/pricing/tts.py`. Same shape as stt.py.
  `calculate_tts_cost(model, character_count) -> Decimal`.
- [ ] Create `voicegateway/pricing/catalog.py`. Unified facade.
  Dispatches by modality. Replaces the current pricing dict.

### 2.3 — Wire into CostTracker

- [ ] Add `pricing_source: str` field to `RequestRecord`
  (`voicegateway/storage/models.py`).
- [ ] Add `pricing_source` column to SQLite schema. Migration script
  for existing DBs (or a one-time conversion at startup).
- [ ] Update `CostTracker.calculate_cost()` to dispatch by modality
  via `voicegateway/pricing/catalog.py`.
- [ ] Update `InstrumentedSTT|LLM|TTS` to capture and pass through
  `pricing_source` to logged requests.

### 2.4 — Surface pricing source

- [ ] Add `pricing_source` to `/v1/costs` response (already part of
  the `include_pricing_source` query param work in Phase 4, but the
  field needs to exist in the schema now).
- [ ] Add `pricing_source` to dashboard request log view (light
  touch — text column, no new charts).

### 2.5 — Fix the placeholder bug

- [ ] Fix `groq/llama-3.1-8b` $0.0 placeholder in the new local
  catalog. Use Groq's actual paid-tier pricing.

### 2.6 — Staleness gate

- [ ] Add unit test: every STT/TTS catalog entry's
  `pricing_source_date` must be ≤ 60 days old. Test fails CI if any
  entry stale, forcing a manual refresh with each release.

### 2.7 — Tests

- [ ] Unit tests for `voicegateway/pricing/llm.py`: known model,
  unknown model, edge cases (zero tokens, very large counts).
- [ ] Unit tests for `voicegateway/pricing/stt.py` and `tts.py`:
  known model, unknown model, source-date metadata present.
- [ ] Unit tests for `voicegateway/pricing/catalog.py`: modality
  dispatch correctness, fallback behavior when modality is unknown.
- [ ] Verify all existing cost-tracking tests still pass.

### 2.8 — Phase 2 verification

- [ ] `make test` passes; coverage ≥ 75%.
- [ ] Commit Phase 2 milestone tag locally (`v0.1.0-phase2`).

---

## Phase 3 — Streaming Validation (Week 3, May 17–23)

Build fixture-based test suite for streaming cost accounting.

### 3.1 — Fixture recording infrastructure

- [ ] Create `scripts/record-streaming-fixtures.py`. CLI tool gated
  behind `--record` flag. Hits real provider APIs with a test prompt,
  saves response with provider-reported usage. Saves to
  `tests/fixtures/streaming/<provider>_<model>_<modality>_<batch|stream>_<date>.json`.
- [ ] Create `tests/fixtures/streaming/` directory with README
  explaining how fixtures are recorded and how to refresh them.
- [ ] Add `.env.fixtures.example` documenting required API keys for
  recording (OPENAI_API_KEY, DEEPGRAM_API_KEY, CARTESIA_API_KEY).
  These are NOT used in CI — fixtures are committed.

### 3.2 — Record minimum fixture set

- [ ] Record OpenAI gpt-4o-mini batch + stream fixtures.
- [ ] Record Deepgram nova-3 batch + stream fixtures.
- [ ] Record Cartesia sonic-3 batch + stream fixtures.
- [ ] (Stretch) Record Anthropic claude-haiku batch + stream.
- [ ] (Stretch) Record AssemblyAI batch + stream.
- [ ] (Stretch) Record ElevenLabs batch + stream.

### 3.3 — Replay test suite

- [ ] Add `respx` (or equivalent HTTP mocking) to dev dependencies.
- [ ] Create `tests/test_streaming_cost_accounting.py`. For each
  fixture: replay through VG's wrapper, assert (a) input/output
  units counted match provider-reported usage, (b) calculated cost
  matches `genai-prices`/local catalog calculation for those units,
  (c) TTFB hook fires correctly during streaming.
- [ ] Verify: replay tests pass on all recorded fixtures.

### 3.4 — TTFB hook hardening

- [ ] Add a focused test: TTFB hook captures the timestamp at the
  moment the first content chunk arrives, not at request issuance.
  Currently the audit found this is manual and easy to break across
  modalities — make sure the test fails if the hook is missing.
- [ ] If the test surfaces a real bug in TTFB capture, fix it.

### 3.5 — Phase 3 verification

- [ ] All replay tests pass.
- [ ] Coverage on `InstrumentedSTT|LLM|TTS` streaming paths reaches
  80%+ (per design doc success gate).
- [ ] Commit Phase 3 milestone tag locally (`v0.1.0-phase3`).

---

## Phase 4 — Reconciliation Tooling (Week 4, May 24–30)

CSV export, reconciliation CLI, /v1/costs enhancements.

### 4.1 — `/v1/costs` enhancements

- [ ] Add `?per_modality=true` query parameter. Returns separate
  STT/LLM/TTS sums.
- [ ] Add `?include_pricing_source=true` query parameter. Adds source
  attribution per line.
- [ ] Add `?start=` and `?end=` ISO date parameters. Replaces fixed
  `period=today|week|month` (keep the old `period` param for
  backward compat — both can coexist).
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
