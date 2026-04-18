---
layout: home

hero:
  name: VoiceGateway
  text: Voice AI Gateway for AI-Native Teams
  tagline: Self-hosted STT, LLM, and TTS routing. Manage from your coding agent via MCP.
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
  - icon: "\U0001F399"
    title: Unified STT + LLM + TTS
    details: Route all three modalities through one gateway. 11 providers including Deepgram, OpenAI, Anthropic, Cartesia, ElevenLabs, Groq, AssemblyAI, Ollama, Whisper, Kokoro, Piper.
    link: /guide/core-concepts
    linkText: Learn how it works

  - icon: "\U0001F916"
    title: First-Class MCP Server
    details: 17 tools let Claude Code, Cursor, and Codex manage your gateway conversationally. Add providers, create projects, check costs — from your terminal.
    link: /mcp/
    linkText: Explore MCP

  - icon: "\U0001F3E0"
    title: Self-Hosted, Your Data
    details: Docker Compose in five commands. API keys encrypted locally with Fernet. No cloud dependencies.
    link: /examples/docker-deployment
    linkText: Deploy in minutes

  - icon: "\U0001F4B0"
    title: Budget Enforcement
    details: Per-project daily budgets with warn, throttle, or block actions. Never get surprised by a provider bill again.
    link: /examples/budget-enforcement
    linkText: Configure budgets

  - icon: "\U0001F500"
    title: Automatic Fallbacks
    details: Primary provider down? Gateway falls back automatically. Cloud outage? Switch to local. Your agent keeps running.
    link: /examples/fallback-chains
    linkText: Set up fallbacks

  - icon: "\U0001F4CA"
    title: Production Observability
    details: Per-request TTFB, per-project costs, Prometheus metrics, audit logs. Built-in dashboard at localhost:9090.
    link: /configuration/observability
    linkText: Monitor your stack
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
