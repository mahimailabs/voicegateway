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

---

## 2026-05-04 00:30 UTC — docs(sweep): fix four critical surface bugs from the audit

Files: `dashboard/frontend/index.html` (C4), `docs/reference/faq.md` (C3 backup advice rewrite), `docs/reference/troubleshooting.md` (H4 env var rename), `docs/examples/fallback-chains.md` + `docs/examples/local-only.md` + `docs/examples/basic-voice-agent.md` (M4 VoiceAssistant -> AgentSession), `.agents/TODO.md` (1.3.5b marked `[x]`).
Tests: n/a (markdown + HTML; no Python touched).

Per-fix notes:

- **C4: dashboard `<title>`.** `dashboard/frontend/index.html:6` was `<title>LiveKit Inference Gateway</title>`, copying LiveKit's product name. Replaced with `<title>VoiceGateway Dashboard</title>`. One-line fix; verified by grep that no other `LiveKit Inference Gateway` strings exist in `dashboard/`.
- **C3: FAQ backup advice.** Old advice was `cp ~/.config/voicegateway/voicegw.db ~/backups/...` annotated as "safe while gateway is running -- SQLite uses WAL mode." But `grep PRAGMA voicegateway/storage/sqlite.py` returns only `PRAGMA table_info`; WAL is NOT set. Rewrote the section to use `sqlite3 ~/.config/voicegateway/voicegw.db ".backup ~/backups/..."`. The `.backup` command is atomic, respects SQLite's locking protocol, and works regardless of journal mode. Explicitly states "VoiceGateway does not enable WAL journaling" so a reader who reads the previous advice elsewhere knows the rewrite is intentional. Updated the cron example too.
- **H4: troubleshooting env var name.** `docs/reference/troubleshooting.md:119` referenced `VOICEGW_ENCRYPTION_KEY`. The canonical name in `voicegateway/core/crypto.py` is `VOICEGW_SECRET`. One-word rename, plus an inline pointer to crypto.py and the fallback resolution order (env -> `~/.config/voicegateway/.secret` -> auto-generated) so a confused reader can follow up.
- **M4: VoiceAssistant -> AgentSession.** `docs/examples/fallback-chains.md` line 198 had `from livekit.agents.voice_assistant import VoiceAssistant`, which raises `ImportError` on `livekit-agents>=1.5.0` (the project's pinned floor in `pyproject.toml:20`). Rewrote that example using the modern `AgentSession` + `Agent` + `silero.VAD` pattern from `examples/basic_agent.py`. Found two more files with the same bug via grep (`docs/examples/local-only.md:107,127` and `docs/examples/basic-voice-agent.md:68,91`); rewrote both. The credibility-issues entry M4 explicitly listed this as in-scope: "Same fix applicable in any other doc that imports VoiceAssistant."

Verifications run after edits:
- `grep VoiceAssistant docs/` -> no files found.
- `grep VOICEGW_ENCRYPTION_KEY docs/` -> no files found.
- `grep "LiveKit Inference Gateway" dashboard/` -> no matches.

The remaining matches in `.agents/credibility-issues.md` and `.agents/TODO.md` are intentional quotes of the audit findings, kept verbatim.

No em dashes introduced.

---

## 2026-05-04 00:55 UTC — docs(sweep): align STT/TTS model IDs to local catalog

Files: 14 docs files updated via `replace_all` Edits across three sweeps; `.agents/TODO.md` (1.3.5c marked `[x]` with completion note + three new discovered-work items).
Tests: n/a (markdown only).

Three sweeps done in this iteration:

- **`whisper/large-v3` to `local/whisper-large-v3`** across 14 files: `docs/api/dashboard-api.md`, `docs/api/python-sdk.md`, `docs/examples/fallback-chains.md`, `docs/examples/local-only.md`, `docs/examples/budget-enforcement.md`, `docs/examples/multi-project.md`, `docs/configuration/models.md`, `docs/architecture/middleware.md`, `docs/configuration/providers.md`, `docs/configuration/stacks.md`, `docs/architecture/gateway-core.md`, `docs/configuration/voicegw-yaml.md`, `docs/mcp/tools/observability.md`, `docs/mcp/tools/projects.md`.
- **`whisper/base` to `local/whisper-base`** across 3 files: `docs/examples/local-only.md`, `docs/configuration/models.md`, `docs/configuration/providers.md`.
- **`kokoro/default` to `local/kokoro`** across 9 files: `docs/examples/fallback-chains.md`, `docs/examples/local-only.md`, `docs/examples/budget-enforcement.md`, `docs/examples/multi-project.md`, `docs/architecture/gateway-core.md`, `docs/configuration/stacks.md`, `docs/configuration/providers.md`, `docs/configuration/voicegw-yaml.md`, `docs/configuration/models.md`.

All three sweeps now match `voicegateway/pricing/catalog.py` and the model registry in `voicegw.example.yaml:66-76, 140-146`. Verified clean via grep `\bwhisper/large-v3|\bwhisper/base|kokoro/default`: no matches remain in `docs/`.

Why `replace_all` was safe: the catalog also has `groq/whisper-large-v3` (the Groq-hosted Whisper) which was a collision concern. Confirmed via grep that the substring `whisper/large-v3` (slash form) does not appear inside `groq/whisper-large-v3` (the latter uses `whisper-large-v3` with a hyphen). Same logic for `whisper/base`. Replace_all distinguished them cleanly.

Deferred per the original TODO note ("Consider deferring LLM ID alignment until Phase 2"):

- `anthropic/claude-sonnet-4-20250514` (16 doc occurrences) vs catalog `anthropic/claude-3.5-sonnet`. Phase 2 (genai-prices) carries the newer ID natively; deferring means the docs' newer IDs become accurate in Phase 2 rather than requiring a downward sweep now.
- `groq/llama-3.3-70b-versatile` (13 doc occurrences) vs catalog `groq/llama-3.1-70b`. Same reasoning.

Other model-ID inconsistencies surfaced while reading the 14 files (none fixed in this iteration; all added to `.agents/TODO.md` discovered-work for Phase 2 evaluation):

- `kokoro/kokoro-v1` in `docs/api/dashboard-api.md` and `docs/mcp/tools/projects.md`.
- `assemblyai/best` and `assemblyai/nano` in `docs/configuration/models.md`.
- `groq/llama-3.1-8b-instant` in `docs/configuration/models.md` and `docs/configuration/providers.md`.
- `ollama/llama3`, `ollama/mistral` in `docs/configuration/models.md` and `docs/migration/from-livekit-inference.md`.
- `anthropic/claude-haiku-3-5` in `docs/configuration/models.md`.
- `piper/en_US-lessac-medium`, `piper/en_US-amy-low` (treats voice as part of model ID; catalog uses just `local/piper`).

Side effect of the kokoro sweep: the YAML blocks now read `local/kokoro:` with a stale `model: default` line below. The schema (`voicegateway/core/schema.py:30-34`) uses `extra="allow"` so the field is harmless, but it is non-canonical relative to `voicegw.example.yaml:140-142` which has no `model:` field on `local/kokoro:`. Captured as a separate discovered-work item.

No em dashes introduced in this iteration.

---

## 2026-05-04 01:15 UTC — docs(faq): tighten coverage, perf, multi-instance, and Postgres claims

Files: `docs/reference/faq.md` (four targeted edits), `.agents/TODO.md` (1.3.5d marked `[x]` with completion note).
Tests: n/a (markdown only).

Per-edit notes:

- **H2 (coverage drift).** Line 5 said `200+ tests with over 70% code coverage`. Aligned to the actual CI gate: `200+ tests with 75%+ code coverage enforced by CI (pyproject.toml sets fail_under = 75)`. Names the file and the setting so a reader can verify it themselves.
- **H3 (unbacked perf numbers).** Removed the four bulleted figures (microseconds for routing, ~1ms for cost-tracking write, ~1ms for budget check, nanoseconds for latency monitoring) and the `under 5ms total` claim. Replaced with prose that names the actual operations VG does per request (config-dict resolution, async SQLite write, async SQLite read on cache miss, timestamp diff), surfaces the architectural facts ("no extra network hop and no inter-process boundary", "cost-tracking writes are non-blocking", "budget check is cached in memory with a 30-second TTL so most requests do not hit the database"), and explicitly disclaims that VG ships no benchmark suite. Anyone needing a precise figure is told to run one for their own hardware. Replaces unmeasured precision with measured architectural framing.
- **M1 (multi-instance budget cache caveat).** Appended one paragraph to the K8s scaling section after the existing "Important" callout: with separate per-replica DBs, the in-memory budget cache does not sync across replicas. A project-wide daily budget cannot be strictly enforced across instances. Single-instance is the supported topology for project-wide budgets; a shared backend is v0.3+ scope. Closes the audit Section 2b finding that the docs glossed over this structural ceiling.
- **M2 (Postgres "planned").** Line 233 said `Switch to a different storage backend (PostgreSQL support is planned)`. Tightened to `(PostgreSQL is v0.3+ scope; until then VG runs on a single instance for write workloads)`. Matches design doc §9 ordering and gives a reader a concrete answer to "when?".

H1 deferred per plan: line 5 still reads "v0.1.0 (alpha)" while the published PyPI version is 0.0.3. This will be accurate after Phase 4.5 bumps `pyproject.toml` to 0.1.0; keeping the deferral avoids a duplicate edit at release time.

No em dashes introduced; verified by spot-check on the four edits.

---

## 2026-05-04 01:30 UTC — docs(migration): add date attribution to LiveKit cost comparison; defer version-upgrades.md to Phase 4.5

Files: `docs/migration/from-livekit-inference.md` (cost-comparison section), `.agents/TODO.md` (1.3.5e marked `[x]` with completion note).
Tests: n/a (markdown only).

Per-fix notes:

- **H6 (cost-comparison attribution).** Inserted a blockquote between the introductory paragraph and the table: "Provider pricing snapshot as of 2026-05-04. Verify against each provider's pricing page (Deepgram, OpenAI, Cartesia) and the LiveKit Cloud dashboard before basing a migration decision on these numbers; provider rates change." This gives any reader who lands on a stale snapshot a clear instruction (verify against provider pages) and a date to check against. Also did two micro-cleanups in the same section: the `| -- |` placeholder in the table swapped to `| n/a |` (clearer intent, and `--` may render as en-dash via VitePress smart-typography); and the trailing prose `eliminates the inference markup -- you pay provider prices directly` became `eliminates the inference markup: you pay provider prices directly` (colon for the same effect, no risk of em-dash rendering).
- **L3 (version-upgrades.md).** Deferred to Phase 4.5 release-prep per the original TODO plan. Reading the current file confirms it is not empty: it has an "Upgrade process" section, a versioning policy section, and a placeholder v0.1.0 stanza with release date `2026-04-17` and "no breaking changes to migrate from" (which was true for the actual `v0.0.3` initial release this stanza was written for). Phase 4.5 will rewrite the v0.1.0 stanza with the real v0.1.0 deltas (genai-prices integration, framing reframe, reconciliation tooling, fallback-doc reframing, etc.) and align the release date. No edits in this iteration.

Phase 1.3 is now structurally complete: hero (1.3 #1), from-litellm rewrite (1.3 #2), decision-tree page (1.3 #3), first-agent prerequisites (1.3 #4), and credibility sweep (1.3 #5 split across 1.3.5a-e) all done. Three deferrals are explicit and tracked: H1 FAQ version stamp (Phase 4.5), L3 version-upgrades stanza (Phase 4.5), and the LLM model-ID sweep (Phase 2 genai-prices). Phase 1.4 (LiveKit FallbackAdapter docs page) starts in the next iteration.

No em dashes introduced.

---

## 2026-05-04 01:50 UTC — docs(examples): create LiveKit FallbackAdapter integration guide

Files: `docs/examples/livekit-fallback-adapter.md` (new, ~80 lines), `docs/.vitepress/config.mts` (one sidebar entry inserted between "Fallback Chains" and "Local-Only Stack"), `.agents/TODO.md` (1.4 marked `[x]`).
Tests: n/a (markdown + config; will be exercised at docs build in 1.5).

API surface verified before writing the page: used the LiveKit Docs MCP (`mcp__claude_ai_Livekit_Docs__docs_search` and `mcp__claude_ai_Livekit_Docs__get_pages` against `/reference/agents/events/?agents-sdk=python`). Confirmed:

- `from livekit.agents import llm, stt, tts` exposes `FallbackAdapter` per modality.
- API signature is `stt.FallbackAdapter([provider1, provider2, ...])` etc.
- Behavior: failed request resubmitted to next provider, failed provider marked unhealthy, periodic background recheck, traffic shifts back when primary recovers.
- `AgentSession` emits `ErrorEvent` with `error.recoverable` flag when chain is exhausted (`False`) or successfully advanced (`True`).
- `stt.FallbackAdapter` is Python-only (called out in the "When this is not what you need" section); LLM and TTS adapters work on Node.js too.

Page structure addresses all four TODO sub-bullets:

- **(a) what triggers fallback** in the "What triggers fallback" section, citing the LiveKit reference for the canonical behavior list.
- **(b) cost-tracking interaction** in "How VoiceGateway's cost tracking interacts": each attempt is logged as a separate `RequestRecord` (primary as `status=error`, secondary as `status=success`); explicit note that the `fallback_from` field is populated by VG's resolver-time chain, not by `FallbackAdapter`; clarifies that both attempts count against the project budget independently because providers count failed requests too.
- **(c) recommended chain patterns** in "Recommended chain patterns": cloud-to-cloud-to-local, modality-specific latency guidance (lowest-latency provider first for STT and TTS; quality-first for LLM), local anchor for true outage coverage.
- **(d) why this over VG's own** in "Why LiveKit FallbackAdapter, not VG's own": the three reasons (already exists in same framework, LiveKit team maintains it, integrates with `ErrorEvent` flow). Plus a "When this is not what you need" section pointing back to VG's resolver-time chain for startup-only selection cases.

The working code snippet uses canonical model IDs from `voicegateway/pricing/catalog.py` and `voicegw.example.yaml` (deepgram/nova-3, groq/whisper-large-v3, local/whisper-large-v3, openai/gpt-4.1-mini, anthropic/claude-3.5-sonnet, ollama/qwen2.5:3b, cartesia/sonic-3, elevenlabs/eleven_turbo_v2_5, local/kokoro). All are priceable today via the catalog.

Sidebar entry added to `docs/.vitepress/config.mts` `/examples/` block, positioned after "Fallback Chains" and before "Local-Only Stack" so the resolver-time + runtime pair sits together in nav order.

Phase 1.4 complete. Phase 1.5 (verification) starts in the next iteration: run docs build, fix any broken links, commit Phase 1 milestone tag locally.

No em dashes in this iteration's outputs.

---

## 2026-05-04 02:10 UTC — chore(verify): Phase 1 verification gates pass; tag v0.1.0-phase1

Files: `docs/.vitepress/config.mts` (ignoreDeadLinks updated), `.agents/TODO.md` (1.5 four sub-items resolved: three `[x]`, one `[~]` skipped with reason), local git tag `v0.1.0-phase1`.
Tests: see verification matrix below.

Verification matrix:

| Gate | Command | Result |
|---|---|---|
| Docs build | `cd docs && npm run build` | First run failed: 1 dead link to `/guide/cost-reconciliation` (Phase 4.4 deliverable). After ignoreDeadLinks update: builds clean in 3.0s. |
| Ruff | `uv run ruff check voicegateway dashboard tests` | All checks passed |
| Mypy | `uv run mypy voicegateway dashboard` | Success: no issues found in 56 source files |
| Pytest | `uv run coverage run -m pytest tests/ --ignore=tests/providers/test_ollama.py` | 255 passed, 4 skipped |
| Coverage | `uv run coverage report` | TOTAL 79% (above the 75% gate in `pyproject.toml:97`) |

The dead-link fix: VitePress's link checker found `/guide/cost-reconciliation` referenced from `docs/migration/from-litellm.md` (the migration doc rewritten in iteration 9) which points to a page that Phase 4.4 will create. Added the path to `ignoreDeadLinks` with a comment noting the entry can be removed when the page lands.

Docs deploy verification skipped: the preview-branch deploy requires `docs.yml` GitHub Actions workflow to fire on the `feat/cost-track-rebuild` branch; that's mahimairaja's call after a push. Local build success is the strongest signal I can produce from this iteration.

Tagged `v0.1.0-phase1` locally pointing at the verification commit. Tag is **not** pushed (per design-doc Decision Log: "Local v0.1.0 tag, not pushed; mahimairaja decides when to publish").

**Phase 1 (framing) is complete.**

Phase 1 deliverables shipped:
- README hero + features + badges + install instructions all rewritten with the wedge reframe (iter 4-7).
- `docs/index.md` hero + feature grid match the new framing; "Why VoiceGateway" prose + competitive table dropped (iter 8 + 13).
- `docs/migration/from-litellm.md` rewritten from competitive to complementary; LiteLLM's audio endpoints acknowledged (iter 9).
- `docs/guide/decision-tree.md` created with honest matrix; sidebar entry added (iter 10).
- `docs/guide/first-agent.md` has the explicit LiveKit Server Setup section (iter 11).
- `docs/examples/livekit-fallback-adapter.md` created with API verified via the LiveKit Docs MCP; sidebar entry added (iter 18).
- Credibility sweep: runtime-fallback over-promise reframed across 8 files (iter 13), four critical surface bugs fixed (iter 14), STT/TTS model IDs aligned to local catalog across 14 files (iter 15), FAQ accuracy claims tightened (iter 16), LiveKit cost-comparison snapshot dated (iter 17).

Three explicit deferrals tracked in discovered-work for later phases:
- H1 (FAQ "v0.1.0 alpha" version stamp) -> Phase 4.5 release-prep.
- L3 (`version-upgrades.md` v0.1.0 stanza rewrite) -> Phase 4.5 release-prep / CHANGELOG.
- LLM model-ID sweep (`anthropic/claude-sonnet-4-20250514`, `groq/llama-3.3-70b-versatile`) -> Phase 2 (genai-prices may resolve them upstream).

Phase 2 (pricing foundation) starts in the next iteration.

No em dashes in this iteration's outputs.

---

## 2026-05-04 02:25 UTC — chore(research): document genai-prices integration surface for Phase 2

Files: `.agents/JOURNAL.md` (this entry), `.agents/TODO.md` (Phase 2.1 #1 marked `[x]`).
Tests: n/a (research only).

Researched `pydantic/genai-prices` via the context7 MCP (`resolve-library-id` then `query-docs` against `/pydantic/genai-prices`), and pulled the latest PyPI version via `WebFetch` against `pypi.org/pypi/genai-prices/json`. Below is the integration surface VoiceGateway needs for Phase 2.

### Package facts

- PyPI name: `genai-prices` (Python package import: `genai_prices`).
- Latest version on PyPI as of 2026-05-04: **0.0.57** (uploaded 2026-04-21).
- Install: `uv add genai-prices` or `pip install genai-prices`. Optional CLI: `pip install "genai-prices[cli]"`.
- Pinning per design doc §5.1: target `>=0.0.52,<0.1` (allows minor catalog updates; caps before any 0.1.0 schema change). 0.0.57 is well within this range.
- Bundled price catalog ships with each version; `UpdatePrices` (covered below) optionally fetches fresher data from GitHub at runtime.

### Core API: `calc_price`

```python
from genai_prices import Usage, calc_price

usage = Usage(input_tokens=1000, output_tokens=100)
price = calc_price(usage, model_ref='gpt-4o', provider_id='openai')

if price is None:
    # model/provider not matched
    ...
else:
    print(price.total_price, price.input_price, price.output_price)
    print(price.model.name, price.provider.name)
```

Signature:
- `calc_price(usage: Usage, model_ref: str, *, provider_id: str | None = None, provider_api_url: str | None = None, timestamp: datetime | None = None) -> PriceCalculation | None`
- Returns `None` when the model/provider cannot be matched. **No exception.** This is the path VG must handle.

`Usage` fields (per docs):
- `input_tokens: int` (required-ish; default 0)
- `output_tokens: int`
- `cache_write_tokens: int` (Anthropic-style prompt caching)
- `cache_read_tokens: int`
- `input_audio_tokens: int` (multimodal: GPT-4o-audio etc.)
- `output_audio_tokens: int`

`PriceCalculation` fields:
- `total_price: float` (USD)
- `input_price: float` (USD)
- `output_price: float` (USD)
- `model: Model` with `.name`, `.provider_name`
- `provider: Provider` with `.name`
- `auto_update_timestamp` (set when prices were freshened by `UpdatePrices`)

Provider matching alternatives if `provider_id` is unknown:
- `provider_api_url`: pass the API base URL; library matches against its provider catalog.
- Both `provider_id` and `provider_api_url` are optional; either or neither works, with reduced match precision when neither is set.

### Auto-update: `UpdatePrices`

Background updater that periodically fetches the latest catalog from `https://raw.githubusercontent.com/pydantic/genai-prices/main/prices/data.json`.

```python
from genai_prices import UpdatePrices, Usage, calc_price

with UpdatePrices() as updater:
    updater.wait()  # blocks until first fetch completes
    price = calc_price(Usage(input_tokens=1000, output_tokens=100), 'gpt-4o')
```

Defaults: 1-hour interval. Configurable via `UpdatePrices(update_interval=1800, url=...)`. Manual control via `.start(wait=True)` / `.stop()`. Async helpers `wait_prices_updated_async` and `wait_prices_updated_sync` exist for callers without an `UpdatePrices` reference.

**Decision for v0.1.0:** do NOT wire `UpdatePrices` into VoiceGateway. Reasoning: (a) the bundled catalog updates with each release of `genai-prices`, so a redeploy refreshes prices anyway; (b) a background thread fetching from GitHub on every VG instance is opt-in rather than default; (c) air-gapped users can pin a version. If users ask for fresh pricing without a redeploy, we can add `UpdatePrices` as an opt-in feature in a later release. v0.1.0 ships with bundled data only.

### Integration surface for VoiceGateway

`voicegateway/pricing/llm.py` will wrap genai-prices behind a small synchronous function:

```python
from decimal import Decimal
from typing import Optional
from genai_prices import Usage, calc_price

def calculate_llm_cost(
    model_ref: str,
    input_tokens: int,
    output_tokens: int,
    provider_id: Optional[str] = None,
) -> Optional[Decimal]:
    """Return total LLM cost in USD as Decimal, or None if model not in genai-prices."""
    usage = Usage(input_tokens=input_tokens, output_tokens=output_tokens)
    price = calc_price(usage, model_ref=model_ref, provider_id=provider_id)
    if price is None:
        return None
    # genai-prices returns float; convert to Decimal via str() to preserve digits.
    return Decimal(str(price.total_price))
```

Notes on the wrapper design:

- **Decimal vs float.** genai-prices returns `float`. VoiceGateway's `RequestRecord.cost_usd` is currently `float`; design doc §5.1 doesn't explicitly mandate Decimal. Returning `Decimal` from this wrapper future-proofs Phase 4's reconciliation tooling (sum-of-decimals avoids float-rounding drift across thousands of requests). Conversion via `Decimal(str(float))` rather than `Decimal(float)` because the latter introduces binary-rounding artifacts.
- **None-on-unknown.** Per design-doc §5.1 ("Returns `None` if model not in their catalog (no silent zero)"), the wrapper passes through `None`. The caller (`CostTracker`) must decide what to log: `cost_usd=0.0` with a warning, or skip the record. Recommend: log a warning ("LLM model X not in genai-prices catalog") and store `cost_usd=0.0` so the request still appears in dashboards but cost is honestly absent.
- **`pricing_source` value.** Per design-doc §5.1, the field reads `genai-prices@<version>`. `genai_prices.__version__` exposes the package version (need to verify in the wrapper); fallback to `importlib.metadata.version("genai-prices")` if not.
- **Provider hint.** VG's model IDs are `provider/model` (e.g., `openai/gpt-4o-mini`). We can split on `/` to derive `provider_id` and pass it to `calc_price` for higher-precision matching. The wrapper signature should accept the `provider_id` separately so the caller (which already has the parsed `ModelId`) does not have to re-split.
- **Cache and audio tokens.** v0.1.0 ignores cache_*_tokens and *_audio_tokens. Per design doc §3, advanced features are out of scope. The wrapper signature stays narrow (input + output only); we can broaden later without a breaking change.

### Open questions for the implementation iterations (not blocking)

- Does `genai_prices.__version__` exist as a top-level attribute? If not, use `importlib.metadata.version("genai-prices")`.
- Does `calc_price` accept a `Usage` object for STT/TTS workloads at all? Probably not — STT and TTS have different unit shapes (audio-seconds, characters) that genai-prices does not model. Hence the design's modality split: LLM via genai-prices, STT/TTS via local catalog.
- Pricing snapshot timestamp: do we surface `price.auto_update_timestamp` in the `pricing_source` string when it is set? For v0.1 (no `UpdatePrices`), the timestamp is None and the source string just says `genai-prices@<version>`.

Phase 2.1 #2 (add the dependency to pyproject.toml) and #3 (uv lock + fresh-venv verification) follow in the next iterations.

No em dashes in this iteration's outputs.

---

## 2026-05-04 02:35 UTC — feat(deps): add genai-prices runtime dependency to pyproject.toml

Files: `pyproject.toml` (one line added to `dependencies`), `.agents/TODO.md` (Phase 2.1 #2 marked `[x]`).
Tests: pyproject.toml parses cleanly via `python3 -c "import tomllib"`; full deps list verified.

Added `"genai-prices>=0.0.52,<0.1"` to the runtime `dependencies` array. Pin per design doc §5.1: lower bound `0.0.52` matches the design's stated minimum; upper bound `<0.1` caps before any potential 0.1.0 schema break. Latest published version on PyPI is `0.0.57` (per the previous iteration's research), which falls cleanly inside this range.

This is the only new external dependency approved for v0.1.0 per `.agents/PROPMT.md` ("Never introduce a new external dependency without an explicit TODO.md task approving it. genai-prices is the one approved new dep this release."). The pin is conservative on both ends:

- The lower bound `>=0.0.52` is design-doc-stated; we do not yet know if older versions of genai-prices have the `Usage` shape and `calc_price` signature documented in the previous iteration's research. Lowering to `>=0.0.5` or `>=0.0.40` could be done later if a real user reports trouble with 0.0.52.
- The upper bound `<0.1` reflects pre-1.0 software where minor-version bumps may break the API. Once genai-prices ships 0.1.0, we can re-test, validate the API shape, and bump.

Phase 2.1 #3 (`uv lock` to refresh the lockfile + fresh-venv install verification) is the next iteration.

No em dashes in this iteration's outputs.

---

## 2026-05-04 02:50 UTC — chore(deps): refresh uv.lock with genai-prices; verify fresh-venv install; remove broken milestone tag

Files: `uv.lock` (regenerated to include genai-prices 0.0.57 and its 5 transitive deps), `pyproject.toml` (no net change; raw-options experiment reverted), `.agents/TODO.md` (Phase 2.1 #3 marked `[x]`; one new discovered-work item; iteration 19 milestone-tag entry annotated to record the deletion). Local tag `v0.1.0-phase1` deleted.
Tests: `uv lock` (134 packages resolved); fresh-venv install via `uv venv /tmp/voicegw-fresh-venv --seed --clear` + `VIRTUAL_ENV=/tmp/voicegw-fresh-venv uv pip install -e ".[dev,dashboard,mcp]"` succeeded; smoke test `from genai_prices import Usage, calc_price; calc_price(Usage(input_tokens=1000, output_tokens=100), model_ref='gpt-4o', provider_id='openai')` returned `total_price = 0.0035` matching the expected GPT-4o pricing ($0.0025 for 1k input + $0.001 for 100 output).

Verifications:

- **uv.lock:** Regenerated. genai-prices 0.0.57 added with its transitive deps (none required new top-level entries; genai-prices is a small package).
- **Fresh venv install:** `voicegateway==0.1.dev77+gd1f815bda` (the auto-generated dev version since no semver tag exists; expected) installed cleanly with the `dev`, `dashboard`, and `mcp` extras.
- **genai-prices runtime:** `genai_prices.__version__` exposes `'0.0.57'` as a module attribute, answering one of the open questions from iteration 20: the wrapper can use `genai_prices.__version__` directly for the `pricing_source = "genai-prices@<version>"` string.
- **calc_price smoke test:** GPT-4o priced correctly through the public API. The integration surface documented in iteration 20 holds.

### Side issue: the `v0.1.0-phase1` milestone tag broke hatch-vcs

The literal tag name `v0.1.0-phase1` is not a PEP 440-valid version. setuptools-scm's default `tag_regex` happily matches `v0.1.0-phase1` and extracts the version `0.1.0-phase1`, which then fails PEP 440 validation with `InvalidVersion: Invalid version: 'v0.1.0-phase1'`. This blocked `hatch-vcs prepare_metadata_for_build_editable`, which `uv lock` calls during dependency resolution.

I tried two fixes inside `[tool.hatch.version.raw-options]`:

```toml
tag_regex = '^v(?P<version>\d+\.\d+\.\d+)$'
fallback_version = "0.1.0.dev0"
```

Neither change made `uv lock` pass. The error switched from `InvalidVersion` to `AssertionError: Error getting the version from source vcs:` with no further detail. Either `raw-options` are not piped through to setuptools-scm by hatch-vcs in the version this project uses, or the keys need a different name. Spent ~10 minutes on this; declined to dig deeper since the practical answer is to delete the broken tag.

`SETUPTOOLS_SCM_PRETEND_VERSION=0.1.0.dev0 uv lock` works as an env-var override (already used by the Dockerfile per the conversation summary), but that does not help local dev without setting the env var every time.

**Resolution:** deleted the local tag (`git tag -d v0.1.0-phase1`). With no tag at all, hatch-vcs falls back to its default behavior of generating `0.1.dev<N>+g<hash>` from the most recent commit, which is what the install showed (`0.1.dev77+gd1f815bda`). pyproject.toml `raw-options` experiment reverted; net diff to pyproject.toml from this iteration is zero (only the genai-prices line from iteration 21 remains).

The Phase 1 milestone is captured in:

- Commit `bf42481` (the chore(verify) verification commit).
- Iteration 19's journal entry titled "Phase 1 verification gates pass; tag v0.1.0-phase1".

Marked the iteration-19 milestone-tag TODO entry with an explicit "tag was created then deleted" note.

Added a discovered-work item asking: which milestone-tag scheme should ceremonial markers (Phase 1 complete, Phase 2 complete, etc.) use? Options listed: (a) drop the `v` prefix (`phase1-complete`); (b) PEP 440 local segment (`v0.1.0+phase1`); (c) hatch-vcs `tag_regex` restriction + `fallback_version` (needs the right config syntax). Until mahimairaja decides, milestone markers stay in journal entries only. Phase 2-4 do not depend on the tag.

### Phase 2.1 complete

All three sub-items done: research (iter 20), pyproject.toml dep (iter 21), uv lock + fresh-venv verification (this iter). Phase 2.2 (pricing module split) starts in the next iteration.

No em dashes in this iteration's outputs.
