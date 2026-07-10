---
title: Examples
description: Runnable examples showing how to use VoiceGateway with LiveKit, Pipecat, Docker, and more.
---

# Examples

Practical, runnable examples for common VoiceGateway use cases. Each example includes working code and the configuration it needs.

Before running any example, install VoiceGateway with the providers you need:

<CodeGroup>
```bash uv
uv add "voicegateway[openai,deepgram,cartesia]"
```

```bash pip
pip install "voicegateway[openai,deepgram,cartesia]"
```
</CodeGroup>

Then create a config file:

```bash
voicegw init
```

<CardGroup cols={2}>
  <Card title="Basic Voice Agent" icon="microphone" href="/examples/basic-voice-agent">
    Minimal metered agent using LiveKit and Pipecat side by side.
  </Card>
  <Card title="LiveKit: attach + guard" icon="shield-check" href="/examples/livekit-attach-guard">
    Native LiveKit plugins metered by `attach(session)` with a guarded LLM.
  </Card>
  <Card title="Pipecat: attach + guard" icon="shield-check" href="/examples/pipecat-attach-guard">
    Native Pipecat services metered by an Observer with a guarded LLM.
  </Card>
  <Card title="Budget Enforcement" icon="dollar-sign" href="/examples/budget-enforcement">
    Enforce a daily spend cap with `guard(llm, budget="$5.00/day")`.
  </Card>
  <Card title="Fallback Chains" icon="arrow-right-arrow-left" href="/examples/fallback-chains">
    Startup-time provider selection: walk a chain and pick the first that builds.
  </Card>
  <Card title="LiveKit FallbackAdapter" icon="rotate" href="/examples/livekit-fallback-adapter">
    Runtime error-driven failover using LiveKit's built-in FallbackAdapter.
  </Card>
  <Card title="Local-Only Deployment" icon="server" href="/examples/local-only">
    Zero cloud dependencies: Whisper + Ollama + Kokoro, no API keys.
  </Card>
  <Card title="Multi-Project" icon="folder-open" href="/examples/multi-project">
    Per-project cost attribution with `attach(..., project=...)`.
  </Card>
  <Card title="Docker Deployment" icon="docker" href="/examples/docker-deployment">
    Production-ready Docker Compose with health checks and optional Ollama.
  </Card>
  <Card title="Claude Code Integration" icon="robot" href="/examples/claude-code-integration">
    Manage providers, projects, and costs from Claude Code via the MCP server.
  </Card>
  <Card title="OpenRTC Multi-Agent" icon="users" href="/examples/openrtc-multi-agent">
    Track every agent in one worker with the OpenRTC SessionObserver seam.
  </Card>
</CardGroup>
