---
title: VoiceGateway
description: Cost tracking, observability, and control for LiveKit and Pipecat voice agents. Attach one line to your existing agent and see per-modality spend land in the dashboard.
---

VoiceGateway meters what your voice agents actually cost. It attaches to an agent you
already run on **LiveKit Agents** or **Pipecat**, records LLM tokens, STT audio-minutes,
and TTS characters per request, prices them through `voice-prices`, and reconciles the
totals against your provider invoices.

The core is framework-neutral: `import voicegateway` pulls neither framework. Two seams do
the work. [`attach()`](/guide/attach) observes (passive: cost and latency).
[`guard()`](/guide/guard) controls (active: fallback, rate limits, budgets).

<CardGroup cols={2}>
  <Card title="What is VoiceGateway" icon="circle-question" href="/guide/what-is-voicegateway">
    The problem it solves and where it fits in a voice stack.
  </Card>
  <Card title="Self-host quickstart" icon="rocket" href="/guide/quick-start">
    Install, attach to your LiveKit or Pipecat agent, see costs in minutes.
  </Card>
  <Card title="Hosted Cloud" icon="cloud" href="/hosted/quickstart">
    Skip the daemon. Point your agent at the hosted collector.
  </Card>
  <Card title="CLI reference" icon="terminal" href="/cli/index">
    Every `voicegw` command: serve, dashboard, costs, reconcile.
  </Card>
  <Card title="API reference" icon="code" href="/api/index">
    Python SDK, HTTP API, MCP server, dashboard API, architecture.
  </Card>
  <Card title="Decision tree" icon="signs-post" href="/guide/decision-tree">
    Self-host or Cloud, attach or guard: pick the right path.
  </Card>
</CardGroup>

## Install

Install the extra for the framework you run. Provider plugin extras imply the framework, so
one line pulls the runtime and the plugins you name.

<CodeGroup>

```bash uv
uv pip install "voicegateway[livekit]"     # or [pipecat]
```

```bash pip
pip install "voicegateway[livekit]"        # or [pipecat]
```

</CodeGroup>

## Attach in one line

Build your agent exactly as you do today, then hand the session (LiveKit) or task
(Pipecat) to `attach()`. It detects the framework, meters every request, and writes the
records. Nothing else in your agent changes.

<Tabs>
  <Tab title="LiveKit">
    ```python
    from voicegateway import attach

    session = AgentSession(stt=stt, llm=llm, tts=tts)
    attach(session, project="my-agent")   # meters cost + latency
    ```
  </Tab>
  <Tab title="Pipecat">
    ```python
    from voicegateway import attach

    task = PipelineTask(pipeline)
    attach(task, project="my-agent")      # meters cost + latency
    ```
  </Tab>
</Tabs>

Ready to go deeper? Start with [Self-host quickstart](/guide/quick-start), or read how
[`attach()`](/guide/attach) and [`guard()`](/guide/guard) split observe from control.
