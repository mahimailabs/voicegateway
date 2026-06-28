---
layout: home

hero:
  name: VoiceGateway
  text: Cost tracking and reconciliation for LiveKit voice agents
  tagline: Modality-aware unit accounting. LLM, STT, and TTS prices from voice-prices. Verify against provider invoices with voicegw reconcile.
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
    title: One-line drop-in for livekit.agents.inference
    details: "voicegateway.inference.STT, LLM, TTS mirror LiveKit's inference module signature. Swap the import line; the rest of your agent code keeps working. Cost tracking, latency monitoring, and session correlation happen transparently."
    link: /guide/quick-start
    linkText: See the integration

  - icon: "\U0001F4B0"
    title: Modality-aware unit accounting
    details: "LLM cost per-1k-token, STT cost per-audio-minute, TTS cost per-character. Prices for all three modalities come from voice-prices (a fork of pydantic/genai-prices that covers LLM, STT, and TTS)."
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

## Where VoiceGateway fits

VoiceGateway is purpose-built for LiveKit voice agents that want cost visibility per modality (audio-minutes for STT, tokens for LLM, characters for TTS) and reconciliation against actual provider invoices. For a longer breakdown of which tool fits which workload, see the [decision tree](/guide/decision-tree).

## Install

<CodeGroup>

```bash pip
pip install voicegateway[all]
```

```bash docker
git clone https://github.com/mahimailabs/voicegateway
cd voicegateway
docker compose up -d
```

</CodeGroup>

## Use from Claude Code

```bash
claude mcp add voicegateway --command "voicegw mcp --transport stdio"
```

Now ask Claude Code:

> "Add Deepgram with this API key. Register nova-3 for STT. Create a project for Tony's Pizza with a five dollar daily budget using premium stack."

Done in 30 seconds. No YAML editing, no dashboard clicking.
