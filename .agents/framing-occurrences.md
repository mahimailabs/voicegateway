# Framing Occurrences

Inventory of every place the old generic-gateway framing appears in
the repo. Each Phase 1 task in `.agents/TODO.md` should sweep one or
more buckets below and rewrite per the design-doc wedge:

> "VoiceGateway gives LiveKit voice agents modality-aware cost
> estimation backed by pydantic/genai-prices, plus reconciliation
> tooling so you can verify our numbers against your actual provider
> invoices."

Search terms used: `self-hosted inference gateway`, `inference gateway`,
`LLM gateway`, `AI gateway`, `voice AI gateway`, `gateway for`,
`unified STT|LLM|TTS|inference|routing`.

---

## 1. User-facing docs (Phase 1.3 — rewrite required)

| File | Line | Quote |
|---|---|---|
| `README.md` | 5 | `**Self-hosted inference gateway for voice AI.**` (hero subtitle) |
| `README.md` | 6 | `**Unified STT + LLM + TTS routing. Your API keys. Local models included. Agent-managed via MCP.**` |
| `README.md` | 21 | `Every LLM gateway routes LLMs. None routes the full voice pipeline...` |
| `README.md` | 25 | comparison table title row (`LiteLLM \| Cloudflare AI Gateway \| LiveKit Inference (Cloud) \| **VoiceGateway**`) |
| `docs/index.md` | 6 | `text: Voice AI Gateway for AI-Native Teams` (hero) |
| `docs/index.md` | 21 | `title: Unified STT + LLM + TTS` (feature card) |
| `docs/index.md` | 59 | `Every existing LLM gateway routes LLMs. Nobody routes the full voice pipeline...` |
| `docs/guide/what-is-voicegateway.md` | 3 | `VoiceGateway is a **self-hosted inference gateway** purpose-built for voice AI applications.` |
| `docs/architecture/index.md` | 3 | `VoiceGateway is a self-hosted inference gateway for voice AI...` |
| `docs/.vitepress/config.mts` | 5 | `description: 'Self-hosted inference gateway for voice AI. STT, LLM, TTS with agent-native MCP management.'` (site `<meta>`) |
| `docs/reference/changelog.md` | 7 | `**Initial release** of VoiceGateway -- a self-hosted inference gateway for voice AI.` |
| `docs/mcp/index.md` | 3 | `...inspect, configure, and manage your voice AI gateway...` (soft, but still old framing) |
| `docs/migration/from-litellm.md` | 141 | `You now have unified cost visibility across STT, LLM, and TTS -- something LiteLLM cannot provide.` (stale claim flagged by audit — LiteLLM now has STT/TTS) |
| `docs/migration/version-upgrades.md` | 43 | `Gateway core -- unified routing for STT, LLM, and TTS requests through ...` |

## 2. Distribution metadata (PyPI listing, Docker Hub) — Phase 1.2/1.3

These show up in package indexes and container registries, so they
shape first-impression search results.

| File | Line | Quote |
|---|---|---|
| `pyproject.toml` | 4 | `description = "Self-hosted inference gateway for voice AI — route STT, LLM, and TTS to any provider or local model"` |
| `Dockerfile` | 49 | `org.opencontainers.image.description="Self-hosted inference gateway for voice AI with MCP"` |
| `.github/workflows/docker-publish.yml` | 76 | metadata `description=Self-hosted inference gateway for voice AI with MCP` |
| `.github/workflows/docker-publish.yml` | 104 | `short-description: "Self-hosted inference gateway for voice AI with MCP"` (Docker Hub short description) |
| `docker/README.voicegateway.md` | 3 | `Self-hosted inference gateway for voice AI. Unified STT + LLM + TTS routing with MCP server for coding agents.` (Docker Hub README) |
| `docker/README.dashboard.md` | 3 | `Web dashboard for [VoiceGateway](https://hub.docker.com/r/mahimairaja/voicegateway) -- self-hosted inference gateway for voice AI.` |

## 3. User-facing code (CLI help, server `/docs`, package metadata) — Phase 1 sweep

These render via `voicegw --help`, `pip show voicegateway`, FastAPI
auto-generated `/docs`, etc.

| File | Line | Quote |
|---|---|---|
| `voicegateway/__init__.py` | 1 | `"""VoiceGateway — self-hosted inference gateway for voice AI."""` |
| `voicegateway/cli.py` | 16 | `help="VoiceGateway — self-hosted inference gateway for voice AI"` (Typer app help) |
| `voicegateway/server.py` | 39 | `description="HTTP API for the VoiceGateway self-hosted inference gateway."` (FastAPI title) |
| `voicegateway/core/gateway.py` | 31 | `"""Self-hosted inference gateway for voice AI agents.` (Gateway class docstring) |

## 4. Dashboard frontend — Phase 1.3

| File | Line | Quote |
|---|---|---|
| `dashboard/frontend/index.html` | 6 | `<title>LiveKit Inference Gateway</title>` — **WRONG title entirely; copies LiveKit's product name. Independent credibility issue, surface to credibility-issues.md as well.** |
| `dashboard/frontend/src/pages/Overview.tsx` | 21 | `subtitle="Live voice AI gateway stats"` |

## 5. Internal / agent-facing (lower priority)

`CLAUDE.md:7` describes the project for the LLM. Update once new
framing is locked so future Claude Code sessions inherit the right
framing. Not strictly user-facing, but it shapes every future agent
session.

| File | Line | Quote |
|---|---|---|
| `CLAUDE.md` | 7 | `VoiceGateway — a self-hosted inference gateway for voice AI. Provides unified routing for STT, LLM, and TTS...` |

## 6. Intentional historical references (do NOT edit)

Per design doc §6 success gate ("No 'self-hosted inference gateway'
framing left **except in intentional historical contexts**"), the
following occurrences must stay:

| File | Line | Why preserved |
|---|---|---|
| `docs/audit-2026-05-02.md` | 289, 483 | Audit report that diagnosed the framing problem. Editing it would erase the diagnostic record. |
| `docs/design/v0.1.0.md` | 47, 198 | Design doc explicitly quotes the old framing as the thing being replaced. |
| `.agents/JOURNAL.md` | 15 | First-iteration journal entry quoting old framing for the wedge-reframe note. |
| `.agents/TODO.md` | 23, 37, 54, 60 | Task descriptions referring to the old framing. |
| `.agents/PROPMT.md` | 37, 141 | Operating-prompt instructions quoting the old framing. |

## Summary counts

- Active occurrences to fix: **24** across 22 files.
- Intentional historical occurrences: **9** across 5 files.
- Distribution-metadata occurrences (PyPI/Docker Hub visible): **6** — fix early so search-index snapshots flip quickly.
- Source-of-truth strings to update once: `pyproject.toml:4` propagates to PyPI; `Dockerfile:49` + the docker-publish workflow propagate to Docker Hub; `docs/.vitepress/config.mts:5` propagates to the docs site `<meta>`.
- Bonus credibility issue: `dashboard/frontend/index.html:6` has `<title>LiveKit Inference Gateway</title>` — wrong product name. Will be cross-listed in `credibility-issues.md` (Phase 1.1 task #3).
