---
title: Examples
description: Complete, runnable VoiceGateway agents for LiveKit and Pipecat, plus the local-only deployment pattern.
---
Two complete agent files, plus the local-only and Docker deployment patterns.
Before running any example, install VoiceGateway for your framework and the
provider plugins your agent uses; see [Installation](/guide/installation).

<CardGroup cols={2}>
  <Card title="First agent (LiveKit)" icon="microphone" href="/guide/first-agent">
    The complete LiveKit worker: `attach()` for cost metering, `guard()` for LLM fallback and a spend cap.
  </Card>
  <Card title="Pipecat: attach + guard" icon="shield-check" href="/examples/pipecat-attach-guard">
    The same pattern as a complete, runnable Pipecat pipeline.
  </Card>
  <Card title="Local-Only Deployment" icon="server" href="/examples/local-only">
    Zero-cost LLM via Ollama, and what local STT/TTS actually requires.
  </Card>
  <Card title="Docker Deployment" icon="docker" href="/examples/docker-deployment">
    Production-ready Docker Compose with health checks and optional Ollama.
  </Card>
</CardGroup>
