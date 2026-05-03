# VoiceGateway v0.1 — Implementation Journal

Append-only log. One entry per Ralph Loop iteration. Newest entries
at the bottom.

---

## 2026-05-03 20:55 UTC — chore(design): record key decisions from docs/design/v0.1.0.md

Files: `.agents/JOURNAL.md` (new), `.agents/TODO.md` (Phase 1 task 1.1 #1 marked `[x]`).
Tests: n/a (no code changed).

Notes — design-doc decisions captured for downstream iterations:

- **Wedge reframe.** Old: "self-hosted inference gateway." New: "modality-aware cost estimation backed by pydantic/genai-prices, plus reconciliation tooling for LiveKit voice agents." Honest about scope and accuracy. Disclaimer ("LLM costs estimated, may drift up to 5%") is positioned as a feature.
- **Pricing source split.** LLM costs come from `pydantic/genai-prices` (hard dependency, not vendored — 1,100+ models, 33 contributors, monthly releases, historic price tracking). STT and TTS stay in a local catalog under `voicegateway/pricing/{stt,tts}.py` with `pricing_source_date: date` and `pricing_source_url: str` per entry. `voicegateway/pricing/catalog.py` is a unified facade dispatching by modality.
- **Source attribution.** New `RequestRecord.pricing_source: str` field — `"genai-prices@<version>"` for LLM, `"voicegateway-catalog@YYYY-MM-DD"` for STT/TTS. Surfaced in `/v1/costs`, `voicegw reconcile`, and dashboard request-log view.
- **Staleness gate.** CI test fails if any STT/TTS catalog entry's `pricing_source_date` is >60 days old. Forces a manual refresh per release.
- **Substitute validation, not dogfooding.** Streaming cost paths verified by fixture replay: `tests/fixtures/streaming/<provider>_<model>_<modality>_<batch|stream>_<date>.json` recorded once via `scripts/record-streaming-fixtures.py --record` (dev-only, real API keys required, NOT in CI). Replayed in CI via respx. mahimairaja explicitly opted out of dogfooding-as-gate; CHANGELOG must disclose the limitation.
- **Drop runtime fallback engineering.** LiveKit's `FallbackAdapter` already covers this at the plugin layer. Ship `docs/examples/livekit-fallback-adapter.md` showing the recommended composition pattern (cloud→cloud→local) instead of building VG's own runtime fallback.
- **New CLIs.** `voicegw export-costs --start --end --project --format csv|json` (per-request line items) and `voicegw reconcile --provider --start --end --provider-usage-file --format text|csv|json` (per-model diff vs provider invoice).
- **`/v1/costs` enhancements.** Add query params `per_modality`, `include_pricing_source`, `start`, `end`. Keep `period=today|week|month` for backward compatibility.
- **Phase plan.** 1=framing fixes (Week 1), 2=pricing foundation (Week 2), 3=streaming validation (Week 3), 4=reconciliation tooling (Week 4).
- **Slip plan.** Phases 1-2 are the minimum viable v0.1.0. Phases 3-4 may defer to v0.1.1, but only with mahimairaja's explicit approval (a commit to TODO.md or a comment in PROMPT.md).
- **Tag, do not push.** Local `v0.1.0` tag at completion. mahimairaja decides PyPI publish.
- **Out of scope (do not pursue even if tempting):** OpenAI-compatible HTTP shim, Postgres backend, multi-instance scaling, multi-tenancy, key rotation tooling, additional providers, dashboard filter/export improvements beyond CLI, OpenTelemetry exporter, real production dogfooding, launch artifacts (blog post, Show HN, Reddit, Twitter, screencast).
- **Hard rules to remember.** Single approved new dependency = `pydantic/genai-prices`. Public Python API (`Gateway`, `ModelId`, `GatewayConfig`) stays backward-compatible. Existing tests are not deleted to make new code pass. No `Co-Authored-By` trailer on commits. Conventional commit format. Branch is `feat/cost-track-rebuild`.
- **Content task quality bar.** No "delve into," "leverage," "robust," "seamless," "comprehensive," "underscore," "noteworthy," "essential" unless precise for context. No scaffolding phrases ("It's important to note that..."). Specific over vague. Honest over promotional.

---

## 2026-05-03 21:05 UTC — chore(audit): inventory generic-gateway framing across the repo

Files: `.agents/framing-occurrences.md` (new), `.agents/TODO.md` (Phase 1 task 1.1 #2 marked `[x]`).
Tests: n/a (no code changed).

Notes — 24 active occurrences across 22 files, plus 9 intentional historical references kept verbatim per design-doc §6 success gate. Bucketed by surface for downstream Phase 1 sweeps:

- **User-facing docs (14 occurrences)** — `README.md` (4: hero subtitle line 5, sub-hero line 6, "Every LLM gateway" line 21, comparison table line 25), `docs/index.md` (3: hero text line 6, feature card line 21, "Every existing LLM gateway" line 59), `docs/guide/what-is-voicegateway.md:3`, `docs/architecture/index.md:3`, `docs/.vitepress/config.mts:5` (site `<meta description>`), `docs/reference/changelog.md:7`, `docs/mcp/index.md:3`, `docs/migration/from-litellm.md:141` (also a stale-claim issue), `docs/migration/version-upgrades.md:43`.
- **Distribution metadata (6 occurrences)** — `pyproject.toml:4` (PyPI listing), `Dockerfile:49` (OCI label), `.github/workflows/docker-publish.yml:76,104` (image description + Docker Hub short description), `docker/README.voicegateway.md:3` (Docker Hub README), `docker/README.dashboard.md:3`. These flip search-index snapshots quickly so worth landing early.
- **User-facing code (4 occurrences)** — `voicegateway/__init__.py:1` (package docstring), `voicegateway/cli.py:16` (Typer help), `voicegateway/server.py:39` (FastAPI `/docs` title), `voicegateway/core/gateway.py:31` (Gateway class docstring).
- **Dashboard frontend (2 occurrences)** — `dashboard/frontend/src/pages/Overview.tsx:21` ("Live voice AI gateway stats" subtitle), and `dashboard/frontend/index.html:6` which has `<title>LiveKit Inference Gateway</title>` — **wrong product name entirely (copies LiveKit's branding)**. Cross-listed for `.agents/credibility-issues.md` in the next task.
- **Internal / agent-facing (1 occurrence)** — `CLAUDE.md:7` describes the project for the LLM. Update once new framing is locked so future Claude Code sessions inherit the right framing.
- **Intentional historical (9 occurrences, do not edit)** — audit report (`docs/audit-2026-05-02.md`), design doc (`docs/design/v0.1.0.md`), this journal, and the `.agents/{TODO,PROPMT}.md` files all quote the old framing as the thing being replaced.

Source-of-truth strings worth fixing first: `pyproject.toml:4` (PyPI), `Dockerfile:49` and the docker-publish workflow (Docker Hub), `docs/.vitepress/config.mts:5` (docs site). Single-line edits with high search-index reach.

---

## 2026-05-03 21:20 UTC — chore(audit): inventory docs claims that don't match code

Files: `.agents/credibility-issues.md` (new), `.agents/TODO.md` (Phase 1 task 1.1 #3 marked `[x]`).
Tests: n/a (no code changed).

Notes — audit said "assume 3-5 more" beyond the two known issues. Found 16 more, total 18 across ~14 files. Bucketed by severity:

- **CRITICAL (5).** C1 — runtime fallback over-promised in 8 places (`README.md:237,255`, `docs/index.md:46`, `docs/examples/fallback-chains.md:3,178,193`, `docs/reference/changelog.md:39`, `docs/architecture/middleware.md:160`); fallback is resolver-only per `voicegateway/middleware/fallback.py:21-79`. C2 — LiteLLM has STT/TTS now (verified May 2026); `docs/migration/from-litellm.md:12,13,19,141,153,162` all stale. C3 — `docs/reference/faq.md:175` claims SQLite WAL keeps `cp` safe but `grep PRAGMA voicegateway/storage/sqlite.py` returns only `PRAGMA table_info`; WAL is not set. Backup advice is dangerous. C4 — `dashboard/frontend/index.html:6` `<title>LiveKit Inference Gateway</title>` is wrong product name. C5 — 13 docs reference `anthropic/claude-sonnet-4-20250514` but pricing catalog only has `anthropic/claude-3.5-sonnet`; cost will compute as $0 for the example model. Same class issue with `whisper/large-v3` vs `local/whisper-large-v3`, and `groq/llama-3.3-70b-versatile` vs `groq/llama-3.1-70b`.
- **HIGH (6).** H1 — FAQ claims "v0.1.0 (alpha)" while PyPI is 0.0.3 (will be correct after this loop). H2 — FAQ "over 70%" coverage but `pyproject.toml:97` enforces `fail_under = 75`. H3 — FAQ overhead numbers ("~1ms," "under 5ms") have no benchmark. H4 — `docs/reference/troubleshooting.md:119` references `VOICEGW_ENCRYPTION_KEY` env var that doesn't exist in code; canonical name is `VOICEGW_SECRET` per `voicegateway/core/crypto.py`. H5 — `docs/migration/from-livekit-inference.md:166` repeats the runtime-fallback claim. H6 — LiveKit Cloud Inference cost-comparison table has unattributed pricing.
- **MEDIUM (4).** M1 — multi-instance scaling advice glosses over budget cache divergence. M2 — Postgres "support is planned" is too soft for v0.3+. M3 — first-agent.md prerequisites are thin (TODO 1.3 #4 already covers). M4 — fallback-chains.md imports non-existent `VoiceAssistant` class; LiveKit 1.5+ removed it.
- **LOW (3).** L1 — static "tests-200+_passing" badge will go stale. L2 — Mermaid diagram in fallback-chains primes runtime mental model. L3 — `version-upgrades.md` empty; sync at Phase 4 CHANGELOG.

Verifications run: grep `journal_mode|PRAGMA` (only `PRAGMA table_info` found), grep `voicegw_(uptime|providers|cost|requests)` (Prometheus metric names match FAQ claim — accurate), grep `claude-sonnet-4|claude-3-5|claude-3.5-sonnet` (three competing identifier formats across docs and code).

---

## 2026-05-03 21:35 UTC — docs(readme): rewrite hero with the wedge reframe

Files: `README.md` (lines 5-6 replaced), `.agents/TODO.md` (Phase 1 task 1.2 #1 marked `[x]`).
Tests: n/a (markdown-only change).

Notes — replaced two bold subtitle lines under the title, leaving title, badges, and quick-link nav untouched (those are Phase 1.2 tasks #2-#4).

- **Old line 5:** `**Self-hosted inference gateway for voice AI.**`
- **Old line 6:** `**Unified STT + LLM + TTS routing. Your API keys. Local models included. Agent-managed via MCP.**`
- **New line 5:** `**Cost tracking and reconciliation for LiveKit voice agents.**`
- **New line 6:** `**Modality-aware unit accounting (audio-minutes, tokens, characters). LLM prices from [pydantic/genai-prices](https://github.com/pydantic/genai-prices). Verify against provider invoices with voicegw reconcile.**`

Rationale: design-doc framing is "modality-aware cost estimation backed by pydantic/genai-prices, plus reconciliation tooling so you can verify our numbers against your actual provider invoices" (38 words). Compressed to two bold lines: line 5 is the wedge in 7 words ("Cost tracking and reconciliation for LiveKit voice agents"), line 6 names the three concrete differentiators (modality-aware units, genai-prices source, reconcile command). Avoids banned words ("leverage", "robust", "comprehensive", "seamless"). Surfaces the actual command name (`voicegw reconcile`) so a reader can grep for it.

The "Why VoiceGateway" comparison table at README.md:21-37 still has the old generic-gateway framing and the stale LiteLLM STT/TTS = ❌ row; it will be rewritten by Phase 1.2 #2 in a later iteration.

---

## 2026-05-03 21:50 UTC — docs(readme): rewrite features section, drop competitive table

Files: `README.md` (lines 19-36 replaced with a new four-pillar features section ending at line 59), `.agents/TODO.md` (Phase 1.2 #2 marked `[x]`; one new discovered-work item added).
Tests: n/a (markdown-only change).

Notes (per task spec, lead with the four ordered differentiators, honest tone, link to decision-tree page):

- **Section 1: Returns LiveKit plugin instances directly.** Concrete `gw.stt()/llm()/tts()` example dropping into `AgentSession(stt=, llm=, tts=)`. Names "no proxy hop, no plugin shim, no rewriting" so the differentiation is verifiable from one code block.
- **Section 2: Modality-aware unit accounting.** Names the units (per-1k-token, per-audio-minute, per-character). Names the LLM source (`pydantic/genai-prices`, 1,100+ models) and the local STT/TTS catalog with `pricing_source_date`. States the 60-day staleness gate.
- **Section 3: Reconciliation tooling.** Two CLI invocations (`voicegw export-costs`, `voicegw reconcile`). States the ~5% drift disclaimer and positions reconciliation as the verification path. This is the "honest about accuracy" framing from the design doc.
- **Section 4: MCP server.** 17 tools, dual transport, named agents (Claude Code, Cursor, Codex, Cline). One paragraph.
- **Closing paragraph:** "Is VoiceGateway right for you?" sends text-only LLM use cases to LiteLLM with a link to the decision-tree page (which Phase 1.3 #5 will create). Complementary, not competitive.

Dropped the competitive comparison table (old `README.md:25-36`). The table had three problems flagged by the credibility audit: (1) LiteLLM STT/TTS rows were `❌` but LiteLLM has both as of May 2026; (2) "Fallback chains" `✅` for VG repeated the over-promised runtime-fallback claim; (3) the framing was head-to-head competitive when the design doc calls for complementary positioning. The decision-tree page will replace it with an honest matrix.

Em-dash sweep: removed three em dashes I introduced in the new section (replaced with `: `, `(...)`, and `. `), per CLAUDE.md hard convention. Pre-existing em dashes elsewhere in the repo (e.g., `README.md:93`) are left for the surrounding-task sweep. Added a discovered-work item to flag the broader em-dash cleanup. Journal-entry headers continue to use em dashes to match the prior format and the example in `PROPMT.md:121`; this is the one consistent format-level use, called out in the discovered-work entry for resolution.

---

## 2026-05-03 22:00 UTC — docs(readme): replace static tests badge with dynamic CI status

Files: `README.md` (line 11 badge URL replaced), `.agents/TODO.md` (Phase 1.2 #3 marked `[x]`; one new discovered-work item added).
Tests: n/a (markdown-only change).

Verification of each existing badge:

- **PyPI version** (`https://img.shields.io/pypi/v/voicegateway` → `https://pypi.org/project/voicegateway/`): dynamic via shields.io, target package exists. Accurate. Kept as-is.
- **Python 3.11+** (static `python-3.11+-blue` → `python.org/downloads/`): matches `pyproject.toml:7` (`requires-python = ">=3.11"`) and the `Programming Language :: Python :: 3.11/3.12/3.13` classifiers at lines 14-16. Accurate. Kept as-is.
- **License: MIT** (static `license-MIT-green` → `LICENSE`): matches `pyproject.toml:6` (`license = "MIT"`). Accurate. Kept as-is.
- **Tests** (was static `tests-200+_passing-brightgreen` → `tests/`): the static count would silently go stale as tests are added (flagged in `credibility-issues.md` L1). Replaced with a dynamic GitHub Actions workflow status badge from `test-coverage.yml`, scoped to `branch=main` so it reflects main-branch state rather than the most recent run on any branch.

New badge: `[![Tests](https://github.com/mahimailabs/voicegateway/actions/workflows/test-coverage.yml/badge.svg?branch=main)](https://github.com/mahimailabs/voicegateway/actions/workflows/test-coverage.yml)`

Rationale for picking `test-coverage.yml` over `ci.yml`: the test-coverage workflow runs on push-to-main and on PRs, so its "passing" state actually reflects what is live on main. `ci.yml` runs on PRs only, so its main-branch state is always stale.

Held back: a Codecov badge could be added (`codecov-action@v5` is already wired at `.github/workflows/test-coverage.yml:62-66`). Captured as discovered-work for the docs sweep or release-prep pass; out of scope for "verify all are current and accurate."
