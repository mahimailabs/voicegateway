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
