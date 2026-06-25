<div align="center">

<img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/banner.svg" alt="VoiceGateway" width="100%" />

<p>
  <a href="https://voicegateway.mahimai.ca/docs"><img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/badges/docs.svg" height="30" alt="Docs"/></a>
  <a href="https://pypi.org/project/voicegateway"><img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/badges/pypi.svg" height="30" alt="PyPI"/></a>
  <img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/badges/python.svg" height="30" alt="Python 3.11+"/>
  <a href="https://docs.livekit.io/agents"><img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/badges/livekit.svg" height="30" alt="LiveKit Agents 1.x"/></a>
  <a href="LICENSE"><img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/badges/license.svg" height="30" alt="MIT License"/></a>
</p>

[**Docs**](https://voicegateway.mahimai.ca/docs) · [**Quick start**](#quick-start) · [**Dashboard**](#the-dashboard) · [**Fleet collector**](#fleet-collector) · [**MCP**](#manage-from-your-coding-agent-mcp)

</div>

```python
from livekit.agents import AgentSession
from voicegateway import inference          # the only line that changed

session = AgentSession(
    stt=inference.STT("deepgram/nova-3"),
    llm=inference.LLM("openai/gpt-4o-mini"),
    tts=inference.TTS("cartesia/sonic-3"),
)
# every call logged: provider, model, tokens, $cost, latency, session_id
```

A drop-in cost and quality observability layer for [LiveKit Agents](https://docs.livekit.io/agents). Use `voicegateway.inference` for VoiceGateway-managed plugins, or `voicegateway.attach(session)` to observe any existing LiveKit STT/LLM/TTS plugin in place. Modality-aware unit accounting (audio-minutes, tokens, characters) with LLM, STT, and TTS prices from [voice-prices](https://github.com/mahimailabs/voice-prices), reconciled against your real provider invoices with one command. Self-hosted. Your keys. No data leaves your infra.

## Why VoiceGateway

Voice AI vendors hide three numbers. VoiceGateway exposes them, per call.

- **Is it working?** Latency p50/p95 across the STT → LLM → TTS loop, interruption rate, dead air, talk-over: the metrics text stacks never have to think about.
- **What does it cost?** STT bills by audio seconds, LLM by tokens, TTS by characters. Every call is broken down by modality and totaled to the cent. `voicegw reconcile` checks recorded numbers against your actual provider invoices.
- **How do I make it cheaper?** Route by combined STT + LLM + TTS latency budget across providers, switch models per call type, attribute cost per tenant so agency clients see only their own usage.

Building a text-only LLM app with no voice component? [LiteLLM](https://docs.litellm.ai/) is the better fit. See the [decision tree](https://voicegateway.mahimai.ca/docs/guide/decision-tree).

## Features

| Capability                     | What it gives you                                                                         |
| :----------------------------- | :---------------------------------------------------------------------------------------- |
| **LiveKit Cloud parity**       | Drop-in for `livekit.agents.inference`. Your keys, your config                            |
| **Voice-conversation metrics** | Per-minute cost, latency p50/p95, interruptions, dead air, talk-over                      |
| **Conversation replay**        | Scrub any past call: STT chunks, LLM tokens, TTS frames with timing and cost              |
| **Terminal UI**                | `voicegw tui` opens a vim-key Textual UI for SSH-in inspection                            |
| **Multi-tenant attribution**   | Per-tenant cost, scoped API keys per team, agency-ready                                   |
| **Cross-modality routing**     | Route by combined latency budget, per-project rosters, white-label branding               |
| **Voice-specific guardrails**  | Real-time PII detection in STT, prompt-injection detection, compliance hooks              |
| **Daemon-first onboarding**    | Curl-bash install, OS daemon, five-question wizard, `voicegw doctor`                      |
| **Fleet collector**            | One-line installer. N agents push to one collector. Slice costs by agent, project, tenant |

Full release history: [CHANGELOG.md](CHANGELOG.md).

## Quick start

```bash
# Single node: local SQLite + the dashboard at http://localhost:8080
pip install "voicegateway[cloud,dashboard]"
voicegw init && voicegw serve
```

Add the three `inference` lines from the snippet above to your agent and every call is tracked. Provider plugins install modularly (`pip install "voicegateway[deepgram,openai,cartesia]"`). The full extras matrix, the zero-install [uvx](https://uvx.sh) path, and the OS daemon installer (LaunchAgent / systemd / Scheduled Task) are in the [get-started docs](https://voicegateway.mahimai.ca/docs/get-started). Python 3.11+.

## The dashboard

A self-hosted web UI at `http://localhost:8080`. Bundled. No SaaS account. No data leaves your stack.

![VoiceGateway Dashboard](https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/dashboard-preview.png)

Overview, Costs (per provider / model / project / tenant, plus latency p50/p95), Sessions (replay, routing decisions, budget overruns), Logs, Agents, and Settings. White-label it per project: upload a logo, set an accent color and product name, and the whole UI re-skins.

## Fleet collector

Run one shared collector on your VPS. Every agent on your fleet pushes telemetry to it: one dashboard, one cost view, across all of them.

```bash
curl -fsSL https://voicegateway.mahimai.ca/collector.sh | bash
```

The script installs Docker if needed, generates and persists secrets, pins the image version, and health-checks the container before returning. Point your agents at it:

```python
from voicegateway.services.sinks import RemoteCollectorSink

sink = RemoteCollectorSink(
    collector_url="https://collector.example.com",
    api_key="<your-ingest-key>",
)
```

SQLite and Postgres backends, plus HTTPS via Caddy: [fleet collector docs →](https://voicegateway.mahimai.ca/docs/deployment/vps)

## Manage from your coding agent (MCP)

VoiceGateway ships a first-class [Model Context Protocol](https://modelcontextprotocol.io) server. Claude Code, Cursor, Codex, and Cline configure providers, create projects, check costs, and tail logs through natural language.

```bash
pipx inject voicegateway "voicegateway[mcp]"
claude mcp add voicegateway --command "voicegw mcp --transport stdio"
```

22 tools across observability, providers, models, and projects. Destructive ops (`delete_*`) require explicit `confirm=True` after a preview. Remote HTTP/SSE transport with bearer auth and the full tool list: [MCP reference →](https://voicegateway.mahimai.ca/docs/mcp/)

## Supported providers

11 providers across cloud and local. Mix and match per call.

| Modality             | Cloud                                         | Local                   |
| :------------------- | :-------------------------------------------- | :---------------------- |
| **STT**              | Deepgram, OpenAI Whisper, AssemblyAI, Groq    | `faster-whisper`        |
| **LLM**              | OpenAI, Anthropic, Groq                       | Ollama (any compatible) |
| **TTS**              | Cartesia, ElevenLabs, Deepgram Aura-2, OpenAI | Kokoro, Piper           |
| **VAD** \*           | Silero                                        | Silero                  |
| **Turn detector** \* | LiveKit MultilingualModel                     | None                    |

\* Configured directly on the LiveKit `AgentSession`, not wrapped by VoiceGateway. The 11-provider count covers STT, LLM, and TTS.

Per-model IDs: [configuration/providers](https://voicegateway.mahimai.ca/docs/configuration/providers). Adding a provider takes about 10 steps: [contributing/adding-a-provider](https://voicegateway.mahimai.ca/docs/contributing/adding-a-provider).

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

Async throughout. Modular provider installs pull only what you use. YAML config with `${ENV_VAR}` substitution. SQLite at the bottom for portability, encrypted with Fernet at rest. [Architecture deep dive →](https://voicegateway.mahimai.ca/docs/architecture/)

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

Built by [Mahimai Raja](https://mahimai.dev), founder of [Mahimai AI](https://mahimai.ca), a voice AI company, in public. Standing on [LiveKit Agents](https://github.com/livekit/agents), [FastAPI](https://fastapi.tiangolo.com/), [Pydantic](https://docs.pydantic.dev/), and [voice-prices](https://github.com/mahimailabs/voice-prices).
