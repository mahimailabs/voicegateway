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

---

## 2026-05-03 22:15 UTC — docs(readme): surface LiveKit prerequisite before the agent example

Files: `README.md` (24 lines inserted between the "Your first agent" heading and the existing Python snippet), `.agents/TODO.md` (Phase 1.2 #4 marked `[x]`).
Tests: n/a (markdown-only change).

Notes — placement and rationale:

- **Placement decision: contextual prereq inside "Your first agent", not a top-of-Quick-Start callout.** The pip / Docker / Compose install paths all work without LiveKit; only the agent example needs it. A top-level prerequisites callout would mislead users running `voicegw status` or `voicegw dashboard` who do not need LiveKit at all. Putting the warning right before the snippet that fails without LiveKit puts it at the actual point of failure.
- **Two paths covered.** LiveKit Cloud (free tier, "sign up + copy creds") and self-hosted via the official `livekit/livekit-server --dev` Docker command, with a link to LiveKit's self-hosting guide for a full setup. Names the default `--dev` keys (`devkey` / `secret`) inline so a reader can paste them straight into the env vars.
- **Names the failure mode verbatim.** "Without these the agent fails with `ConnectionError: Failed to connect`" so a user who hits the error can grep their logs for that exact string and find this section. Same trick the audit recommended.
- **Does not touch the existing Python snippet.** The snippet has a separate latent issue (uses literal `cartesia/sonic-3:voice_id` placeholder for the voice ID) that is part of credibility-issues C5 (model-ID consistency). Out of scope here.
- **No em dashes introduced.** Pre-existing em dash at `README.md:93` (Docker section) is left for the broader em-dash sweep already on the discovered-work list.

Phase 1.2 (README rewrite) is now complete: hero (#1), features (#2), badges (#3), and install instructions (#4) all done. Phase 1.3 (docs site rewrites) starts in the next iteration.

---

## 2026-05-03 22:30 UTC — docs(index): rewrite hero and feature grid to match new README framing

Files: `docs/index.md` (front-matter `hero` + `features` blocks replaced; lines 2-54 rewritten), `.agents/TODO.md` (Phase 1.3 #1 marked `[x]`).
Tests: n/a (markdown-only change; will be exercised when the docs site rebuilds in Phase 1.5).

Notes:

- **Hero text and tagline** now mirror the README hero verbatim. `text: Cost tracking and reconciliation for LiveKit voice agents`. `tagline: Modality-aware unit accounting. LLM prices from pydantic/genai-prices. Verify against provider invoices with voicegw reconcile.` Same wording as `README.md:5-6`.
- **Feature grid collapsed from six to four**, matching the four pillars from README §"Why VoiceGateway":
  1. LiveKit-native plugin returns (icon `\U0001F50C` plug)
  2. Modality-aware unit accounting (icon `\U0001F4B0` money bag, kept from prior grid)
  3. Reconciliation tooling (icon `\U0001F9FE` receipt)
  4. MCP server for agent-managed config (icon `\U0001F916` robot, kept from prior grid)
- **Dropped features** that did not match the wedge or were over-promised: "Unified STT + LLM + TTS" (generic gateway framing), "Self-Hosted, Your Data" (adjacent, not a pillar), "Budget Enforcement" (subsumed under #2 modality-aware accounting), "Automatic Fallbacks" (the C1 over-promise — runtime fallback is LiveKit FallbackAdapter territory, see Phase 1.4 doc), "Production Observability" (adjacent, not a pillar).
- **Forward-pointing links.** Feature #3 `link: /guide/cost-reconciliation` points to a page Phase 4.4 will create. Feature #1 `link: /guide/quick-start`, #2 `link: /configuration/observability`, #4 `link: /mcp/` already exist.
- **YAML quoting.** All four `details:` strings are double-quoted, protecting against any unintended YAML interpretation of commas / parentheses / colons.
- **Out of scope** (handled by 1.3 sweep task): the post-frontmatter "Why VoiceGateway" prose section + competitive comparison table at the bottom of `docs/index.md` still has the old generic-gateway framing and stale LiteLLM No-on-STT/TTS rows. Same content the README used to have; the README dropped its version. Sweep task will harmonize.
- **No em dashes introduced.** Pre-existing em dash at `docs/index.md:47` (in the prose section) is left for the sweep.

---

## 2026-05-03 22:50 UTC — docs(migration): rewrite from-litellm from competitive to complementary

Files: `docs/migration/from-litellm.md` (full rewrite, ~140 lines), `.agents/TODO.md` (Phase 1.3 #2 marked `[x]`).
Tests: n/a (markdown-only change).

Notes:

- **Stale claims fixed.** The old comparison table at lines 12-13 had `STT routing | -- | ...` and `TTS routing | -- | ...` for LiteLLM. Verified false: LiteLLM has shipped `/v1/audio/transcriptions` (Whisper, Deepgram, ElevenLabs Scribe) and `/v1/audio/speech` (OpenAI, Azure, Gemini, ElevenLabs) since early 2026. Old line 141 ("unified cost visibility ... something LiteLLM cannot provide") and the lines-153/162 framing ("when to stay with LiteLLM: text-only" / "when to switch: STT or TTS") all said the same wrong thing. All removed.
- **Reframed from competitive to complementary.** Opening paragraph names LiteLLM's strengths up front (100+ providers, OpenAI-compatible HTTP proxy, multi-level budgets, mature admin UI, audio endpoints). Second paragraph states VoiceGateway is not a replacement, it is a complementary tool for LiveKit voice agents.
- **Replaced the comparison table** with a "Where each one fits" matrix that assigns use cases to the better fit (LiteLLM or VoiceGateway). Both columns are honest: LiteLLM wins on text-only LLM, multi-provider catalog, OpenAI-compat shim, multi-tenant Postgres scale, and budget granularity. VoiceGateway wins on LiveKit voice agents, modality-aware unit accounting, reconciliation, MCP, and local model unification. No more head-to-head checkmarks.
- **Added "Using both together" section.** Acknowledges the most common composition: LiteLLM for non-LiveKit text workloads, VoiceGateway for the LiveKit agent path. Both can read the same provider keys.
- **Added "When to migrate" section.** States the two conditions that must both be true: LiveKit voice agent + want unified per-modality cost tracking with reconciliation. If only #1 is true and current cost tracking is fine, keep LiteLLM and add VG alongside.
- **Migration steps preserved but tightened.** Six steps, each with concrete commands. Step 3 is now explicit that for non-agent text workloads, the user should keep LiteLLM. Step 5 introduces the `voicegw export-costs` and `voicegw reconcile` commands with the ~5% drift disclaimer; this is the cost-rebuild Phase 4 deliverable surfacing in the migration story.
- **Added "A note on the audio endpoints" section.** Explicit acknowledgment that LiteLLM's audio endpoints exist and are well suited to request/response audio (batch transcription, async TTS rendering). Differentiates VG by the LiveKit `AgentSession` plugin-instance shape.
- **Forward-pointing links.** `/guide/decision-tree`, `/guide/cost-reconciliation`, `/examples/livekit-fallback-adapter` all 404 today; created in Phase 1.3 #5 (decision tree), 1.4 (FallbackAdapter), 4.4 (cost reconciliation).
- **No em dashes introduced.** Verified clean via grep on the file.

---

## 2026-05-03 23:10 UTC — docs(guide): create decision-tree page and add to /guide/ sidebar

Files: `docs/guide/decision-tree.md` (new, ~70 lines), `docs/.vitepress/config.mts` (one sidebar entry inserted), `.agents/TODO.md` (Phase 1.3 #3 marked `[x]`).
Tests: n/a (markdown + config; will be exercised at docs build in Phase 1.5).

Notes:

- **Page structure: short answer first, then breakdown.** Six-row matrix at the top maps a user's situation to the right tool: VG (LiveKit voice agent + cost tracking, self-hosted voice with local + cloud unification), LiteLLM (text-only LLM, OpenAI-compat HTTP shim needed), OpenRouter (hosted multi-tenant, no ops), Cloudflare AI Gateway (already on Cloudflare), LiveKit Inference (managed LiveKit Cloud, happy with bundled pricing). Detailed breakdown section gives the rationale per row.
- **Honest "What VoiceGateway is not" section.** Explicitly disclaims four common misconceptions: not an OpenAI-compat HTTP proxy (no `/v1/chat/completions`), not horizontally scaled multi-tenant (SQLite single-writer, per-instance budget caches), not a real-time fallback engine (resolver-time only; refers to LiveKit FallbackAdapter), not a key-rotation system (no MultiFernet / KMS today). This is the page that buys back credibility from the broader audit findings.
- **External links chosen for stability.** LiteLLM (`docs.litellm.ai/`), OpenRouter (`openrouter.ai/`), Cloudflare AI Gateway (`developers.cloudflare.com/ai-gateway/`) are all top-level / well-known URLs unlikely to 404. LiveKit Inference referenced via the LiveKit Cloud landing page (`livekit.io/cloud`) rather than guessing a specific docs path.
- **Sidebar entry added.** `docs/.vitepress/config.mts` had a manually-curated sidebar; the new page would not appear in `/guide/` nav without an entry. Added "Decision Tree" right after "What is VoiceGateway?" and before "Quick Start" so a reader hits "is this for me?" before "how do I install it?". One-line edit; logically scoped to this iteration since the page is otherwise undiscoverable from the in-product nav.
- **Forward-pointing link kept.** `/examples/livekit-fallback-adapter` referenced in the "Not a real-time fallback engine" disclaimer; created in Phase 1.4.
- **No em dashes.** Verified via grep on the new page.

---

## 2026-05-03 23:30 UTC — docs(guide): add LiveKit Server Setup section to first-agent.md

Files: `docs/guide/first-agent.md` (one-line bullet rewritten + new H2 section inserted, ~50 lines added before "Step 1"), `.agents/TODO.md` (Phase 1.3 #4 marked `[x]`).
Tests: n/a (markdown-only change).

Notes:

- **Placement.** New `## LiveKit Server Setup` section sits between the existing Prerequisites bullet list and `## Step 1: Configure VoiceGateway`. Numbered steps preserved verbatim; the new section is unnumbered to avoid renumbering Steps 1-4.
- **Bullet 5 rewritten.** Old: `- A LiveKit server (local or cloud) -- see [LiveKit docs](https://docs.livekit.io/home/get-started/)`. New: `- A LiveKit server (Cloud or self-hosted): setup walkthrough below`. Drops the double-hyphen (which VitePress smart-typography may render as en-dash) and points to the in-page section instead of an external generic doc.
- **Both paths covered.** Option A walks through LiveKit Cloud signup + project creation + credential copy. Option B uses `docker run --rm livekit/livekit-server --dev` with the default `devkey`/`secret` credentials, plus a link to LiveKit's self-hosting guide for production setups.
- **Three env vars surfaced.** Two parallel `export` blocks (Cloud + self-hosted local --dev) so the reader copies the right one. Names the verbatim `ConnectionError: Failed to connect` failure mode so a user hitting that error can grep their logs and find this section.
- **Verify step.** Three `echo` commands print the values; non-empty output means setup is good. No `python -c` assertion (would raise KeyError on missing env vars, cryptic).
- **Out of scope, captured for sweep.** Step 1 onwards still uses `anthropic/claude-sonnet-4-20250514` (credibility issue C5) and the `examples` block at the bottom uses `gw.stack(...)` which returns a tuple via splat (`stt, llm, tts = gw.stack(...)`). The model-ID sweep is Phase 1.3 #5; no action here.
- **No em dashes introduced.** Verified via grep on the file.

---

## 2026-05-03 23:50 UTC — chore: split Phase 1.3 #5 (credibility sweep) into five subtasks

Files: `.agents/TODO.md` (single `[ ]` "Sweep all pages" line replaced with `[ ]` items `1.3.5a` through `1.3.5e`).
Tests: n/a.

Notes — per PROMPT.md ("If a TODO item feels larger, your first action is to break it into smaller items in TODO.md, commit that breakdown as `chore: split <task> into subtasks`, and exit"). The single sweep item covered 14+ remaining credibility-issues across many files; the 30-90 minute / one logical unit / one commit budget could not absorb that.

Five subtasks chosen by mechanism / file cluster:

- **1.3.5a — Runtime-fallback over-promise sweep.** Same-shape claim repeated across README, fallback-chains examples, changelog, middleware architecture, and from-livekit-inference migration. One mental model fix, multiple files. Folds in L2 (Mermaid diagram) and the docs/index.md "Why" prose + comparison table, since both also carry over-promised fallback framing.
- **1.3.5b — Critical surface bug fixes.** Four discrete single-line-ish edits (dashboard `<title>`, SQLite WAL backup advice, `VOICEGW_ENCRYPTION_KEY` to `VOICEGW_SECRET`, broken `VoiceAssistant` import). Each is an independent fix; one commit.
- **1.3.5c — Model-ID consistency sweep.** Single decision (which canonical Anthropic ID) drives 13+ doc edits. Worth its own iteration because the decision interacts with Phase 2 (genai-prices may resolve LLM IDs upstream).
- **1.3.5d — FAQ accuracy claims.** Three accuracy issues plus a multi-instance caveat, all in `docs/reference/faq.md`. One file, one commit.
- **1.3.5e — Final mop-up.** Cost-comparison attribution in from-livekit-inference, plus version-upgrades.md sync deferred to Phase 4 CHANGELOG.

Did NOT split:
- C2 (LiteLLM has STT/TTS) — already done (Iteration 5 dropped the README table, Iteration 9 rewrote from-litellm migration).
- L1 (static "200+ passing" badge) — already done (Iteration 6).
- M3 (first-agent.md prereqs) — already done (Iteration 11).
- H1 (v0.1.0 alpha in FAQ) — self-resolves at release; will be `[~]` deferred to Phase 4.5.

No fix work performed in this iteration. Next iteration picks 1.3.5a.

---

## 2026-05-04 00:10 UTC — docs(sweep): reframe runtime-fallback over-promise as resolver-time

Files: `README.md` (Fallback Chains section), `docs/examples/fallback-chains.md` (hero, Mermaid section, Cloud-to-Local section), `docs/reference/changelog.md:39`, `docs/architecture/middleware.md` (FallbackChain section), `docs/migration/from-livekit-inference.md` (Step 5 fallback chains), `docs/index.md` ("Why VoiceGateway" prose + comparison table replaced with one-paragraph pointer to decision tree), `docs/guide/first-agent.md` (incidental "Using fallbacks" line tightened), `docs/configuration/stacks.md` (incidental "When to use" line tightened), `.agents/TODO.md` (1.3.5a marked `[x]`).
Tests: n/a (markdown-only).

Summary of the reframing applied across all eight files:

- **Old framing:** "automatic failover during a call", "your agent never goes offline", "if Deepgram returns 500s, requests automatically route to Groq".
- **New framing:** "resolver-time fallback at agent startup; once a model is wired into AgentSession, VG does not swap providers mid-call. For runtime/mid-call failover, compose LiveKit's FallbackAdapter."

Per credibility-issues C1, the implementation in `voicegateway/middleware/fallback.py:21-79` only triggers on resolver failure. Everything that read like runtime/error-driven mid-call failover was the over-promise.

Per-file notes:

- **README.md `## Fallback Chains` (was 282-302).** Replaced the "Automatic failover across providers stay running even when a cloud provider has an outage." opener with a resolver-time framing. Replaced the "If Deepgram returns 500s ... Your agent never goes offline." closer with "VoiceGateway does not swap providers mid-call. For runtime failover ... compose LiveKit's FallbackAdapter."
- **docs/examples/fallback-chains.md hero (line 3).** Was "Configure automatic failover ... your voice agent stays available even when a provider goes down." Replaced with a resolver-time description plus a pointer to the FallbackAdapter doc for runtime failover.
- **docs/examples/fallback-chains.md `## How Fallback Works` (lines 86-105 / L2).** Added a one-line caption above the Mermaid diagram clarifying it is construction-time, not call-time. Updated diagram labels: "Try X" -> "Resolve X", "API Error" -> "init error". Updated narrative under the diagram to consistently say "fail to resolve at construction."
- **docs/examples/fallback-chains.md `## Cloud-to-Local Fallback Strategy` (lines 176-193).** Was "ensuring your agent never goes completely offline ... This guarantees that even if all cloud providers are down, your agent can still function using local models." Replaced with two-paragraph honest framing: handles cold-start case (everything unreachable at startup; local model selected), does not handle warm-failure case (mid-call provider degradation). Pointer to FallbackAdapter doc for warm failover.
- **docs/reference/changelog.md:39.** Was "Fallback chains -- per-modality automatic failover when providers are down". Now: "per-modality resolver-time fallback (try the next model if the primary fails to resolve at agent startup; not a runtime/mid-call switch)".
- **docs/architecture/middleware.md `## FallbackChain` (lines 156-160).** Was "Manages automatic failover between models within a modality." Now: full sentence on construction-time semantics, AgentSession lifetime, and the FallbackAdapter pointer for runtime failover.
- **docs/migration/from-livekit-inference.md `### 5. Add fallback chains` (lines 164-167).** Was "VoiceGateway can automatically fail over when a provider is down". Now: "resolver-time fallback ... at agent startup ... that model is then used for the entire call. For runtime/mid-call failover, compose LiveKit's FallbackAdapter."
- **docs/index.md `## Why VoiceGateway` (lines 45-57).** Replaced the entire 13-line prose-plus-comparison-table block with a single one-paragraph "Where VoiceGateway fits" pointer to the decision tree. The competitive table mirrored the README's dropped table; the feature grid in the front-matter and the decision-tree page already cover the same ground.
- **docs/guide/first-agent.md (incidental, line 178).** "If you prefer automatic failover over explicit model selection" tightened to "If you prefer resolver-time fallback (try the next model in the chain when the primary fails to resolve at startup) over explicit model selection". Caught by a follow-up grep after the primary edits.
- **docs/configuration/stacks.md (incidental, line 76).** Same pattern: "automatic failover" tightened to "resolver-time fallback (try the next model if the primary fails to resolve at startup)". Caught by the same grep.

Verification: `grep "automatic failover|never goes offline|automatically route"` across `docs/` and the README returned zero hits afterward (matches in `.agents/TODO.md` and `.agents/credibility-issues.md` are intentional quotes of the old framing in the audit/task descriptions).

No em dashes introduced. The Mermaid diagram in fallback-chains was modernized rather than removed.
