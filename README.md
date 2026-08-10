<div align="center">

<img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/banner.gif" alt="VoiceGateway" width="100%" />

<p>
  <a href="https://docs.voicegateway.dev"><img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/badges/docs.svg" height="30" alt="Docs"/></a>
  <a href="https://pypi.org/project/voicegateway"><img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/badges/pypi.svg" height="30" alt="PyPI"/></a>
  <img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/badges/python.svg" height="30" alt="Python 3.11+"/>
  <a href="https://docs.livekit.io/agents"><img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/badges/livekit.svg" height="30" alt="LiveKit Agents 1.x"/></a>
  <a href="LICENSE"><img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/badges/license.svg" height="30" alt="MIT License"/></a>
</p>

<p>
  <a href="https://discord.gg/ysFaF4uSB"><img src="https://img.shields.io/badge/Discord-Join%20the%20community-5865F2?logo=discord&logoColor=white" alt="Join the VoiceGateway Discord"/></a>
  <a href="https://x.com/voicexprt"><img src="https://img.shields.io/badge/Follow-%40voicexprt-000000?logo=x&logoColor=white" alt="Follow @voicexprt on X"/></a>
  <a href="https://deepwiki.com/mahimailabs/voicegateway"><img src="https://deepwiki.com/badge.svg" alt="Ask DeepWiki about the codebase"/></a>
</p>

[**Docs**](https://docs.voicegateway.dev) · [**Quick start**](#quick-start) · [**Dashboard**](#the-dashboard) · [**Fleet collector**](#fleet-collector) · [**MCP**](#coding-agents-mcp)

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

**The open-source profiler for voice agents.** Add one line and every STT, LLM, and TTS call is priced and timed: cost to the cent, latency p50/p95, and conversation quality. `attach()` takes a [LiveKit](https://docs.livekit.io/agents) `AgentSession` or a [Pipecat](https://github.com/pipecat-ai/pipecat) `PipelineTask`, and `import voicegateway` pulls neither framework until you use it. Prices come from [voice-prices](https://github.com/mahimailabs/voice-prices) and reconcile against your real provider invoices with one command. Self-hosted, your keys, no data leaves your infra.

<details>
<summary><b>Using Pipecat?</b> Same one line.</summary>

```python
from pipecat.pipeline.task import PipelineTask
import voicegateway

task = PipelineTask(pipeline)
voicegateway.attach(task)   # profile every call, Pipecat
```

</details>

## Quick start

```bash
# Local SQLite + the dashboard at http://localhost:8080
pip install "voicegateway[dashboard]"
voicegw init && voicegw serve
```

Add the `voicegateway.attach(session)` line above to your agent and every call is tracked. Provider plugins install with your framework: `pip install "voicegateway[livekit,deepgram,openai,cartesia]"` or `"voicegateway[pipecat]"`.

Python 3.11+. The full extras matrix, the zero-install [uvx](https://uvx.sh) path, and the OS daemon installer are in the [get-started docs](https://docs.voicegateway.dev/get-started).

## What you get

Voice AI vendors hide three numbers: whether it works, what it costs, and how to make it cheaper. VoiceGateway exposes all three, per call.

| Capability                     | What it gives you                                                                          |
| :----------------------------- | :----------------------------------------------------------------------------------------- |
| **Framework-neutral**          | One `attach()` for LiveKit or Pipecat. Your keys, your plugins, no lock-in                  |
| **Voice-conversation metrics** | Per-minute cost, latency p50/p95, interruptions, dead air, talk-over                        |
| **Cost to the cent**           | STT by audio seconds, LLM by tokens, TTS by characters, broken down per call and per model  |
| **Reconciliation**             | `voicegw reconcile` checks recorded cost against your real provider invoices                |
| **Spend control**              | `guard()`: daily budget cap, fallback on error, rate limit, per project                     |
| **Conversation replay**        | Scrub any past call: STT chunks, LLM tokens, TTS frames, with timing and cost               |
| **Multi-tenant attribution**   | Per-tenant cost, scoped API keys per team, agency-ready                                     |
| **Fleet collector**            | One-line installer. N agents push to one collector. Slice by agent, project, tenant         |

Building a text-only LLM app with no voice? [LiteLLM](https://docs.litellm.ai/) is the better fit. See the [decision table](https://docs.voicegateway.dev/guide/what-is-voicegateway#when-something-else-is-the-better-fit). Release history: [CHANGELOG.md](CHANGELOG.md).

## The dashboard

Self-hosted at `http://localhost:8080`. Bundled, no SaaS account, no data leaves your stack.

<div align="center">
  <img src="https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docs/assets/dashboard.png" alt="VoiceGateway dashboard: cost by provider and model" width="100%" />
  <br/>
  <sub>Example numbers. Click through the real thing, no login, at <a href="https://voicegateway.dev/demo">voicegateway.dev/demo</a>.</sub>
</div>

**Overview** (7-day spend and request trend), **Agents** (per-agent cost, model stack, worker memory), **Costs** (per provider, model, project, tenant, plus latency p50/p95), **Calls** (replay any conversation), **Latency**, **Server** (your LiveKit rooms, SIP, egress, cost-annotated), and **Diagnostics** (probe your LiveKit deployment).

White-label it per project: upload a logo, set an accent color and product name, and the whole UI re-skins. One-key light/dark.

## Spend control with `guard()`

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

## Fleet collector

Run one shared collector on your VPS. Every agent pushes to it: one dashboard, one cost view, across all of them.

```bash
curl -fsSL https://voicegateway.dev/collector.sh | bash
```

The script installs Docker if needed, generates and persists secrets, pins the image version, and health-checks the container before returning. Point your agents at it:

```bash
export VOICEGW_COLLECTOR_URL="https://collector.example.com"
export VOICEGW_API_KEY="<your-ingest-key>"
export VOICEGW_PROJECT="my-agent"
```

`attach()` reads those and batches every call to the collector instead of local SQLite. SQLite and Postgres backends, Docker Compose, and HTTPS via Caddy: [deployment docs](https://docs.voicegateway.dev/deployment/vps).

## Coding agents (MCP)

VoiceGateway ships a [Model Context Protocol](https://modelcontextprotocol.io) server, so Claude Code, Cursor, Codex, and Cline can create projects, check costs, and inspect calls in natural language.

```bash
pipx inject voicegateway "voicegateway[dashboard]"
claude mcp add voicegateway --command "voicegw mcp --transport stdio"
```

Destructive ops (`delete_*`) require an explicit `confirm=True` after a preview. Remote HTTP/SSE transport and the full tool list: [MCP reference](https://docs.voicegateway.dev/mcp/).

## Providers

Any provider [voice-prices](https://github.com/mahimailabs/voice-prices) covers. You bring your own native plugins; VoiceGateway meters and prices them.

| Modality | Cloud                                         | Local                   |
| :------- | :-------------------------------------------- | :---------------------- |
| **STT**  | Deepgram, OpenAI Whisper, AssemblyAI, Groq    | `faster-whisper`        |
| **LLM**  | OpenAI, Anthropic, Groq                       | Ollama (any compatible) |
| **TTS**  | Cartesia, ElevenLabs, Deepgram Aura-2, OpenAI | Kokoro, Piper           |

A price it does not recognize records at zero and flags for a rate-card entry, so nothing is silently dropped. Per-model IDs: [configuration/providers](https://docs.voicegateway.dev/configuration/providers).

## Contributing

```bash
git clone https://github.com/mahimailabs/voicegateway
cd voicegateway
pip install -e ".[dev]"
pytest
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before opening a PR. Security issues go through [SECURITY.md](SECURITY.md), not a public issue. Questions and ideas are welcome in [Discord](https://discord.gg/ysFaF4uSB).

<a href="https://github.com/mahimailabs/voicegateway/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=mahimailabs/voicegateway&max=40&columns=10&anon=0" alt="Contributors" />
</a>

## License

[MIT](LICENSE). Fork it, ship it.

Built in public by [Mahimai Raja](https://mahimai.dev), founder of [Mahimai AI](https://mahimai.ca), a voice AI company. Standing on [LiveKit Agents](https://github.com/livekit/agents), [Pipecat](https://github.com/pipecat-ai/pipecat), [FastAPI](https://fastapi.tiangolo.com/), [Pydantic](https://docs.pydantic.dev/), and [voice-prices](https://github.com/mahimailabs/voice-prices).

<!-- GitAds-Verify: B26PKZL6HHS6F2ZU9NAHRIA9OQHS919R -->

## GitAds Sponsored
[![Sponsored by GitAds](https://gitads.dev/v1/ad-serve?source=mahimailabs/voicegateway@github)](https://gitads.dev/v1/ad-track?source=mahimailabs/voicegateway@github)
