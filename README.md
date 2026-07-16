<div align="center">

<img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/banner.gif" alt="VoiceGateway" width="100%" />

<p>
  <a href="https://docs.voicegateway.dev"><img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/badges/docs.svg" height="30" alt="Docs"/></a>
  <a href="https://pypi.org/project/voicegateway"><img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/badges/pypi.svg" height="30" alt="PyPI"/></a>
  <img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/badges/python.svg" height="30" alt="Python 3.11+"/>
  <a href="https://docs.livekit.io/agents"><img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/badges/livekit.svg" height="30" alt="LiveKit Agents 1.x"/></a>
  <a href="LICENSE"><img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/badges/license.svg" height="30" alt="MIT License"/></a>
</p>

[**Docs**](https://docs.voicegateway.dev) · [**Quick start**](#quick-start) · [**Dashboard**](#the-dashboard) · [**Fleet collector**](#fleet-collector) · [**MCP**](#manage-from-your-coding-agent-mcp)

</div>

```python
from livekit.agents import AgentSession
from livekit.plugins import deepgram, openai, cartesia
import voicegateway

session = AgentSession(
    stt=deepgram.STT(model="nova-3"),
    llm=openai.LLM(model="gpt-4o-mini"),
    tts=cartesia.TTS(model="sonic-3"),
)
voicegateway.attach(session)   # one line. profile every call.
# logged per call: provider, model, tokens, $cost, latency, session_id
```

**The open-source profiler for voice agents.** Add one line and every STT, LLM, and TTS call is priced and timed: cost to the cent, latency p50/p95, and conversation quality. `attach()` takes a [LiveKit](https://docs.livekit.io/agents) `AgentSession` or a [Pipecat](https://github.com/pipecat-ai/pipecat) `PipelineTask`; `import voicegateway` pulls neither framework until you use it. Prices come from [voice-prices](https://github.com/mahimailabs/voice-prices) and reconcile against your real provider invoices with one command. Self-hosted. Your keys. No data leaves your infra.

<details>
<summary><b>Using Pipecat?</b> Same one line.</summary>

```python
from pipecat.pipeline.task import PipelineTask
import voicegateway

task = PipelineTask(pipeline)
voicegateway.attach(task)   # profile every call, Pipecat
```

</details>

## Why VoiceGateway

Voice AI vendors hide three numbers. VoiceGateway exposes them, per call.

- **Is it working?** Latency p50/p95 across the STT → LLM → TTS loop, interruption rate, dead air, talk-over: the metrics text stacks never have to think about.
- **What does it cost?** STT bills by audio seconds, LLM by tokens, TTS by characters. Every call is broken down by modality and totaled to the cent. `voicegw reconcile` checks recorded numbers against your actual provider invoices.
- **How do I make it cheaper?** See cost per provider and model so you know what to change. Cap daily spend and fall back on errors with `guard()`. Attribute cost per tenant so agency clients see only their own usage.

Building a text-only LLM app with no voice component? [LiteLLM](https://docs.litellm.ai/) is the better fit. See the [decision tree](https://docs.voicegateway.dev/guide/decision-tree).

## One more line: `guard()`

`attach()` watches. `guard()` acts. Wrap one provider to cap spend, fall back on errors, and rate-limit. It returns a drop-in of the same type, so it slots into your session unchanged.

```python
llm = voicegateway.guard(
    openai.LLM(model="gpt-4o-mini"),
    fallback=[openai.LLM(model="gpt-4o")],   # on a primary error
    budget="$5.00/day",                       # hard stop past the cap
    rate_limit="60/min",
)
```

`guard()` writes no metrics and `attach()` never double-counts, so use both together.

## Features

| Capability                     | What it gives you                                                                         |
| :----------------------------- | :---------------------------------------------------------------------------------------- |
| **Framework-neutral**          | One `attach()` for LiveKit or Pipecat. Your keys, your plugins, no lock-in                 |
| **Voice-conversation metrics** | Per-minute cost, latency p50/p95, interruptions, dead air, talk-over                       |
| **Conversation replay**        | Scrub any past call: STT chunks, LLM tokens, TTS frames with timing and cost              |
| **Spend control**              | `guard()`: daily budget cap, fallback on error, rate limit, per project                   |
| **Reconciliation**             | `voicegw reconcile` checks recorded cost against your real provider invoices              |
| **Terminal UI**                | `voicegw tui` opens a vim-key Textual UI for SSH-in inspection                            |
| **Multi-tenant attribution**   | Per-tenant cost, scoped API keys per team, agency-ready                                    |
| **Fleet collector**            | One-line installer. N agents push to one collector. Slice costs by agent, project, tenant |

Full release history: [CHANGELOG.md](CHANGELOG.md).

## Quick start

```bash
# Single node: local SQLite + the dashboard at http://localhost:8080
pip install "voicegateway[dashboard]"
voicegw init && voicegw serve
```

Add the one `voicegateway.attach(session)` line from the snippet above to your agent and every call is tracked. Provider plugins install with your framework (`pip install "voicegateway[livekit,deepgram,openai,cartesia]"` or `"voicegateway[pipecat]"`). The full extras matrix, the zero-install [uvx](https://uvx.sh) path, and the OS daemon installer (LaunchAgent / systemd / Scheduled Task) are in the [get-started docs](https://docs.voicegateway.dev/get-started). Python 3.11+.

## The dashboard

A self-hosted web UI at `http://localhost:8080`. Bundled. No SaaS account. No data leaves your stack.

<div align="center">
  <img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/dashboard-tour.gif" alt="VoiceGateway dashboard tour" width="100%" />
  <br/>
  <sub>A 7-day spend / requests trend, per-provider cost breakdowns, conversation replay, and one-key light/dark.</sub>
</div>

Overview with a 7-day spend and requests trend, Agents (per-agent cost, model stack, worker memory), Costs (per provider / model / project / tenant, plus latency p50/p95), Calls (replay any conversation), Latency, and Diagnostics (probe your LiveKit deployment). Configure projects and a rate card for billing reconciliation. One-key light/dark theme. White-label it per project: upload a logo, set an accent color and product name, and the whole UI re-skins.

## Fleet collector

Run one shared collector on your VPS. Every agent on your fleet pushes telemetry to it: one dashboard, one cost view, across all of them.

```bash
curl -fsSL https://voicegateway.dev/collector.sh | bash
```

The script installs Docker if needed, generates and persists secrets, pins the image version, and health-checks the container before returning. Point your agents at it with three environment variables:

```bash
export VOICEGW_COLLECTOR_URL="https://collector.example.com/v1/ingest"
export VOICEGW_API_KEY="<your-ingest-key>"
export VOICEGW_PROJECT="my-agent"
```

`attach()` reads them and batches every call to the collector instead of local SQLite. SQLite and Postgres backends, plus HTTPS via Caddy: [fleet collector docs →](https://docs.voicegateway.dev/deployment/vps)

## Manage from your coding agent (MCP)

VoiceGateway ships a first-class [Model Context Protocol](https://modelcontextprotocol.io) server. Claude Code, Cursor, Codex, and Cline create projects, check costs, and inspect calls through natural language.

```bash
pipx inject voicegateway "voicegateway[mcp]"
claude mcp add voicegateway --command "voicegw mcp --transport stdio"
```

Tools across observability, projects, and costs. Destructive ops (`delete_*`) require explicit `confirm=True` after a preview. Remote HTTP/SSE transport with bearer auth and the full tool list: [MCP reference →](https://docs.voicegateway.dev/mcp/)

## Priced providers

VoiceGateway prices calls from any provider [voice-prices](https://github.com/mahimailabs/voice-prices) covers. You bring your own native plugins; VoiceGateway meters and prices them. The common ones:

| Modality | Cloud                                         | Local                   |
| :------- | :-------------------------------------------- | :---------------------- |
| **STT**  | Deepgram, OpenAI Whisper, AssemblyAI, Groq    | `faster-whisper`        |
| **LLM**  | OpenAI, Anthropic, Groq                       | Ollama (any compatible) |
| **TTS**  | Cartesia, ElevenLabs, Deepgram Aura-2, OpenAI | Kokoro, Piper           |

A price it does not recognize records at zero and flags for a rate-card entry, so nothing is silently dropped. Per-model IDs: [configuration/providers](https://docs.voicegateway.dev/configuration/providers).

## Architecture

```mermaid
flowchart TB
    A[Voice Agent · LiveKit or Pipecat] --> B["voicegateway.attach()"]
    B --> F[Middleware Pipeline]
    F --> F1[Cost Tracker]
    F --> F2[Latency Monitor]
    F --> F3[Rate Limiter]
    F --> F4[Multi-tenant Attribution]
    F --> G[(SQLite · encrypted)]
    G --> H[Dashboard UI]
    G --> I[MCP Server]
    I --> J[Claude Code · Cursor · Codex]
```

Async throughout. `import voicegateway` stays framework-neutral: the LiveKit and Pipecat integrations import their framework only on first use. YAML config with `${ENV_VAR}` substitution. SQLite at the bottom for portability, encrypted with Fernet at rest. [Architecture deep dive →](https://docs.voicegateway.dev/architecture/)

## Deploy

<details>
<summary><b>Docker Compose</b> (Postgres + collector)</summary>

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
    image: mahimairaja/voicegateway:latest
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

</details>

For production, the [fleet collector installer](#fleet-collector) handles secrets, image pinning, and health checks for you.

## Contributing

```bash
git clone https://github.com/mahimailabs/voicegateway
cd voicegateway
pip install -e ".[all,dashboard,mcp,dev]"
pytest
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before opening a PR. Security issues go through the disclosure flow in [SECURITY.md](SECURITY.md), not a public issue.

## Community

[![Star History Chart](https://api.star-history.com/svg?repos=mahimailabs/voicegateway&type=Date)](https://star-history.com/#mahimailabs/voicegateway&Date)

<a href="https://github.com/mahimailabs/voicegateway/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=mahimailabs/voicegateway&max=40&columns=10&anon=0" alt="Contributors" />
</a>

## License

[MIT](LICENSE). Fork it, ship it.

Built by [Mahimai Raja](https://mahimai.dev), founder of [Mahimai AI](https://mahimai.ca), a voice AI company, in public. Standing on [LiveKit Agents](https://github.com/livekit/agents), [Pipecat](https://github.com/pipecat-ai/pipecat), [FastAPI](https://fastapi.tiangolo.com/), [Pydantic](https://docs.pydantic.dev/), and [voice-prices](https://github.com/mahimailabs/voice-prices).
