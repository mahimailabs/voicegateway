---
layout: home

hero:
  name: VoiceGateway
  text: Cost tracking and reconciliation for LiveKit voice agents
  tagline: Modality-aware unit accounting. LLM prices from pydantic/genai-prices. Verify against provider invoices with voicegw reconcile.
  image:
    src: /logo.svg
    alt: VoiceGateway
  actions:
    - theme: brand
      text: Get Started
      link: /guide/quick-start
    - theme: alt
      text: View on GitHub
      link: https://github.com/mahimailabs/voicegateway

features:
  - icon: "\U0001F50C"
    title: LiveKit-native plugin returns
    details: "gw.stt(), gw.llm(), gw.tts() return native LiveKit plugin instances. They drop straight into AgentSession with no proxy hop, no plugin shim, and no rewrite of your existing pipeline code."
    link: /guide/quick-start
    linkText: See the integration

  - icon: "\U0001F4B0"
    title: Modality-aware unit accounting
    details: "LLM cost per-1k-token, STT cost per-audio-minute, TTS cost per-character. LLM prices come from pydantic/genai-prices (1,100+ models, monthly releases). STT and TTS live in a local catalog with explicit pricing_source_date metadata."
    link: /configuration/observability
    linkText: How it works

  - icon: "\U0001F9FE"
    title: Reconciliation tooling
    details: "voicegw export-costs and voicegw reconcile compare logged costs against your provider's usage export. Per-request line items carry pricing_source attribution. LLM costs may drift up to ~5%; reconciliation is the verification path."
    link: /guide/cost-reconciliation
    linkText: Walk through reconcile

  - icon: "\U0001F916"
    title: MCP server for agent-managed config
    details: "17 tools (configure providers, create projects with daily budgets, query costs, tail logs, run health checks) over stdio and HTTP/SSE. Claude Code, Cursor, Codex, and Cline can all manage the gateway conversationally."
    link: /mcp/
    linkText: Explore MCP
---

## Why VoiceGateway

Every existing LLM gateway routes LLMs. Nobody routes the full voice pipeline — STT, LLM, AND TTS — through one interface with local model support and first-class MCP. That is the gap VoiceGateway fills.

|                          | LiteLLM | OpenRouter | Portkey | LiveKit Inference | VoiceGateway |
| ------------------------ | :-----: | :--------: | :-----: | :---------------: | :----------: |
| LLM routing              |   Yes   |    Yes     |   Yes   |       Yes         |     Yes      |
| STT routing              |   No    |    No      |   No    |       Yes         |     Yes      |
| TTS routing              |   No    |    No      |   No    |       Yes         |     Yes      |
| Local models             | Partial |    No      |   No    |       No          |     Yes      |
| Self-hostable            |   Yes   |    No      | Partial |       No          |     Yes      |
| MCP server               |   No    |    No      |   No    |       No          |     Yes      |
| LiveKit native           |   No    |    No      |   No    |       Yes         |     Yes      |

## Install

::: code-group

```bash [pip]
pip install voicegateway[all]
```

```bash [docker]
git clone https://github.com/mahimailabs/voicegateway
cd voicegateway
docker compose up -d
```

:::

## Use from Claude Code

```bash
claude mcp add voicegateway --command "voicegw mcp --transport stdio"
```

Now ask Claude Code:

> "Add Deepgram with this API key. Register nova-3 for STT. Create a project for Tony's Pizza with a five dollar daily budget using premium stack."

Done in 30 seconds. No YAML editing, no dashboard clicking.
