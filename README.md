<div align="center">

<img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/src/dashboard/api/static/branding/wordings.png" alt="VoiceGateway" width="320" />

**Voice AI cost transparency. Self-hosted, on your keys.**

[![PyPI version](https://img.shields.io/pypi/v/voicegateway?style=for-the-badge&color=4B8BBE)](https://pypi.org/project/voicegateway)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg?style=for-the-badge)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org)
[![LiveKit Agents 1.x](https://img.shields.io/badge/livekit--agents-1.x-FF5C29?style=for-the-badge)](https://docs.livekit.io/agents)
[![Tests](https://img.shields.io/github/actions/workflow/status/mahimailabs/voicegateway/test-coverage.yml?branch=main&style=for-the-badge&label=tests)](https://github.com/mahimailabs/voicegateway/actions/workflows/test-coverage.yml)
[![GitHub stars](https://img.shields.io/github/stars/mahimailabs/voicegateway?style=for-the-badge&color=FFD700)](https://github.com/mahimailabs/voicegateway/stargazers)

[**Docs**](https://voicegateway.mahimai.ca/docs) · [**Dashboard**](#the-dashboard) · [**Fleet collector**](#fleet-collector) · [**Roadmap**](#roadmap) · [**Contributing**](CONTRIBUTING.md)

</div>

```python
from livekit.agents import AgentSession
from voicegateway import inference          # <- the only line that changed

session = AgentSession(
    stt=inference.STT("deepgram/nova-3"),
    llm=inference.LLM("openai/gpt-4o-mini"),
    tts=inference.TTS("cartesia/sonic-3"),
)
# every call logged: provider, model, tokens, $cost, latency, session_id
```

A drop-in cost and quality observability layer for [LiveKit Agents](https://docs.livekit.io/agents). Modality-aware unit accounting (audio-minutes, tokens, characters) with LLM, STT, and TTS prices from [voice-prices](https://github.com/mahimailabs/voice-prices). Reconcile recorded numbers against your actual provider invoices with one command. Self-hosted. Your keys. No data leaves your infra.

## Why VoiceGateway

Voice AI vendors hide three numbers. VoiceGateway exposes them.

**Is this working?** Voice has metrics text stacks do not: latency p50/p95 across the STT → LLM → TTS loop, interruption rate, dead air, talk-over. The dashboard shows all of them per call.

**What does it cost?** STT bills by audio seconds, LLM bills by tokens, TTS bills by characters. Every call is broken down by modality and totaled to the cent. Run `voicegw reconcile` to verify recorded numbers against your actual provider invoices.

**How do I make it cheaper?** Route by combined STT + LLM + TTS latency budget across providers. Switch models per call type. Per-tenant cost attribution so agency clients see only their own usage.

If you are building a text-only LLM application without a voice component, [LiteLLM](https://docs.litellm.ai/) is likely a better fit. See the [decision tree](https://voicegateway.mahimai.ca/docs/guide/decision-tree).

## What's in the box

| Capability | What it gives you |
|:---|:---|
| **LiveKit Cloud parity** | Drop-in for `livekit.agents.inference`. Your keys, your config |
| **Daemon-first onboarding** | Curl-bash install, OS daemon, five-question wizard, `voicegw doctor` |
| **Terminal UI** | `voicegw tui` opens a vim-key Textual UI for SSH-in inspection |
| **Public-API discipline** | Subpackage layout, CHANGELOG, CONTRIBUTING, SECURITY, explicit `__all__` |
| **Voice-conversation metrics** | Per-minute cost, latency p50/p95, interruptions, dead air, talk-over |
| **Conversation replay** | Scrub any past call. STT chunks, LLM tokens, TTS frames with timing and cost |
| **Multi-tenant attribution** | Per-tenant cost, scoped API keys per team, agency-ready |
| **Cross-modality routing** | Route by combined STT + LLM + TTS latency budget. Per-project rosters. White-label branding |
| **Voice-specific guardrails** | Real-time PII detection in STT, prompt-injection detection, compliance hooks |
| **Fleet collector** | One-line installer. N agents push to one collector. Slice costs by agent, project, tenant |

Full release history: [CHANGELOG.md](CHANGELOG.md).

## Install

```bash
# Single node: local SQLite, runs the dashboard at http://localhost:8080
pip install "voicegateway[cloud,dashboard]"
voicegw init && voicegw serve
```

Or with the OS daemon installer (LaunchAgent / systemd / Scheduled Task):

```bash
curl -fsSL https://voicegateway.mahimai.ca/install.sh | bash
voicegw onboard --install-daemon
```

**Extras:**

```bash
pip install voicegateway                              # core engine only
pip install "voicegateway[cloud]"                     # + cloud provider plugins
pip install "voicegateway[local]"                     # + local runtimes (Whisper, Kokoro, Piper)
pip install "voicegateway[mcp]"                       # + MCP server
pip install "voicegateway[tui]"                       # + voicegw tui
pip install "voicegateway[all,dashboard,mcp,tui]"     # everything
```

**Zero-install one-shot ([uvx](https://uvx.sh)):**

```bash
uvx --from "voicegateway[cloud]" voicegw status
uvx --from "voicegateway[cloud,dashboard]" voicegw serve --port 8080
```

Python 3.11+. Local extras pull larger ML runtimes.

## Fleet collector

Run one shared collector on your VPS. Every agent on your fleet pushes telemetry to it. One dashboard, one cost view, across all of them.

```bash
# Spin up a collector with Postgres backend in one command
curl -fsSL https://voicegateway.mahimai.ca/collector.sh | bash
```

The script installs Docker if needed, generates and persists secrets, pins the image version, and health-checks the container before returning. For non-interactive use:

```bash
# SQLite (single collector, no external database)
curl -fsSL https://voicegateway.mahimai.ca/collector.sh | bash -s -- --sqlite --yes

# Postgres with HTTPS via Caddy
curl -fsSL https://voicegateway.mahimai.ca/collector.sh | bash -s -- --postgres --domain collector.example.com --yes
```

Connect your agents to the collector:

```python
from voicegateway.sinks import RemoteCollectorSink

sink = RemoteCollectorSink(
    collector_url="https://collector.example.com",
    api_key="<your-ingest-key>",
)
```

[Fleet collector docs →](https://voicegateway.mahimai.ca/docs/deployment/vps)

## The dashboard

A self-hosted web UI at `http://localhost:8080`. Bundled. No SaaS account. No data leaves your stack.

![VoiceGateway Dashboard](https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/dashboard-preview.png)

- **Overview** — total requests, cost today, active models, per-project summary cards
- **Costs** — daily spend with per-provider / model / project / tenant breakdown. Latency p50/p95 tab
- **Sessions** — every call, every cost row, routing decisions, budget overruns. Metrics tab
- **Logs** — raw request log with filtering
- **Agents** — per-agent cost and session attribution
- **Settings** — providers, API keys, projects, routing, guardrails, models, audit log

White-label support: upload a logo, pick an accent color, set a product name — the whole dashboard re-skins for your project.

## Manage from your coding agent (MCP)

VoiceGateway ships a first-class [Model Context Protocol](https://modelcontextprotocol.io) server. Claude Code, Cursor, Codex, Cline can configure providers, create projects, check costs, and tail logs through natural language.

**Local (stdio):**

```bash
pipx inject voicegateway "voicegateway[mcp]"
claude mcp add voicegateway --command "voicegw mcp --transport stdio"
```

**Remote (HTTP/SSE with bearer auth):**

```bash
export VOICEGW_MCP_TOKEN=$(openssl rand -hex 32)
voicegw mcp --transport http --port 8090
```

```bash
claude mcp add voicegateway \
  --transport sse \
  --url https://your-host.fly.dev/mcp/sse \
  --header "Authorization: Bearer $VOICEGW_MCP_TOKEN"
```

17 tools exposed: observability, providers, models, projects. Destructive ops (`delete_*`) require explicit `confirm=True` after a preview. [Full MCP reference →](https://voicegateway.mahimai.ca/docs/mcp/)

## Supported providers

11 providers across cloud and local. Mix and match per call.

| Modality | Cloud | Local |
|:---|:---|:---|
| **STT** | Deepgram, OpenAI Whisper, AssemblyAI, Groq, Cartesia | `faster-whisper` |
| **LLM** | OpenAI, Anthropic, Groq | Ollama (any compatible) |
| **TTS** | Cartesia, ElevenLabs, Deepgram Aura-2, OpenAI | Kokoro, Piper |
| **VAD** | Silero | Silero |
| **Turn detector** | LiveKit MultilingualModel | — |

Per-model IDs: [voicegateway.mahimai.ca/docs/configuration/providers](https://voicegateway.mahimai.ca/docs/configuration/providers). Adding a provider takes ~10 steps: [contributing/adding-a-provider](https://voicegateway.mahimai.ca/docs/contributing/adding-a-provider).

## Architecture

```mermaid
flowchart TB
    A[LiveKit Agent] --> B[voicegateway.inference]
    B --> C[Router]
    C --> D[Cloud Providers]
    C --> E[Local Providers]
    B --> F[Middleware Pipeline]
    F --> F1[Cost Tracker]
    F --> F2[Latency Monitor]
    F --> F3[Guardrails]
    F --> F4[Multi-tenant Attribution]
    F --> G[(SQLite · encrypted)]
    G --> H[Dashboard UI]
    G --> I[MCP Server]
    I --> J[Claude Code · Cursor · Codex]
```

Async throughout. Modular provider installs: `pip install "voicegateway[openai,deepgram]"` pulls only what you use. YAML config with `${ENV_VAR}` substitution. SQLite at the bottom for portability; encrypted with Fernet at rest.

[Architecture deep dive →](https://voicegateway.mahimai.ca/docs/architecture/)

## Docker Compose

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: voicegw
      POSTGRES_PASSWORD: ${VOICEGW_PG_PASSWORD}
      POSTGRES_DB: voicegw
    volumes:
      - voicegw-pgdata:/var/lib/postgresql/data
    restart: unless-stopped

  collector:
    image: mahimairaja/voicegateway:0.9.2
    ports:
      - "8080:8080"
    environment:
      VOICEGW_DB_URL: postgresql+asyncpg://voicegw:${VOICEGW_PG_PASSWORD}@postgres/voicegw
    volumes:
      - ./voicegw.yaml:/app/voicegw.yaml:ro
    depends_on: [postgres]
    restart: unless-stopped

volumes:
  voicegw-pgdata:
```

```bash
docker compose up -d
```

Use the [fleet collector installer](#fleet-collector) for production — it handles secrets, image pinning, and health checks automatically.

## HTTP API

```bash
voicegw serve --port 8080
```

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Health check |
| `GET /v1/status` | Provider health + model count |
| `GET /v1/models` · `GET /v1/providers` · `GET /v1/projects` | Resource CRUD |
| `GET /v1/costs?period=today&project=X&tenant=Y` | Cost summary |
| `GET /v1/sessions/{id}/turns` · `/v1/sessions/{id}/replay` · `/v1/sessions/{id}/dead_air` | Voice-conversation surfaces |
| `GET /v1/routing/observations` | Live per-provider latency |
| `GET /v1/api-keys` + CRUD | Per-team scoped keys |
| `GET /v1/audit-log` · `GET /v1/metrics` | Audit + Prometheus metrics |

Full reference: [voicegateway.mahimai.ca/docs/api/http-api](https://voicegateway.mahimai.ca/docs/api/http-api).

## Roadmap

- Enterprise auth, audit log, SOC 2 prep
- One-tap latency probe
- Stability commitment, LTS branch policy

## Contributing

Issues and PRs welcome.

```bash
git clone https://github.com/mahimailabs/voicegateway
cd voicegateway
pip install -e ".[all,dashboard,mcp,dev]"
pytest
```

Before submitting a PR, read [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Security issues go through the disclosure flow in [SECURITY.md](SECURITY.md), not a public issue.

## Stargazers and contributors

[![Star History Chart](https://api.star-history.com/svg?repos=mahimailabs/voicegateway&type=Date)](https://star-history.com/#mahimailabs/voicegateway&Date)

<a href="https://github.com/mahimailabs/voicegateway/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=mahimailabs/voicegateway&max=40&columns=10&anon=0" alt="Contributors" />
</a>

## License

[MIT](LICENSE). Fork it, ship it.

## Built by

[Mahimai Raja](https://mahimai.dev), founder of [Mahimai AI](https://mahimai.ca), a voice AI company. Building VoiceGateway in public.

Built on the shoulders of giants: [LiveKit Agents](https://github.com/livekit/agents), [FastAPI](https://fastapi.tiangolo.com/), [Pydantic](https://docs.pydantic.dev/), [voice-prices](https://github.com/mahimailabs/voice-prices) (a fork of [pydantic/genai-prices](https://github.com/pydantic/genai-prices)), [cryptography](https://cryptography.io/), [Model Context Protocol](https://modelcontextprotocol.io/).
