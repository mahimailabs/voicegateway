---
title: Frameworks and extras
description: VoiceGateway's core is framework-neutral. Pick the extra for the agent framework you run (LiveKit or Pipecat); import voicegateway pulls neither.
---

VoiceGateway's engine core is framework-neutral. A bare `import voicegateway`
imports neither [LiveKit Agents](https://docs.livekit.io/agents/) nor
[Pipecat](https://docs.pipecat.ai/). The record model, pricing (via
`voice-prices`), the storage sinks, and the cost/latency tracker have no
framework dependency. The two seams that touch a framework, `attach()` and
`guard()`, detect the framework of the object you hand them (by module string,
without eagerly importing either) and lazily import only the one they need.

That means you install exactly the framework you run, and nothing else.

## Install the extra you use

<CodeGroup>
```bash uv
# LiveKit Agents
uv pip install "voicegateway[livekit]"

# Pipecat
uv pip install "voicegateway[pipecat]"
```
```bash pip
# LiveKit Agents
pip install "voicegateway[livekit]"

# Pipecat
pip install "voicegateway[pipecat]"
```
</CodeGroup>

VoiceGateway is framework-agnostic and no longer bundles provider or local-model
wheels. Install the LiveKit plugins your agent uses directly (you likely already
have them) alongside `voicegateway[livekit]`, and VoiceGateway meters them by
model_id via voice-prices:

<CodeGroup>
```bash uv
uv pip install "voicegateway[livekit]"
uv pip install livekit-plugins-openai livekit-plugins-deepgram livekit-plugins-cartesia
```
```bash pip
pip install "voicegateway[livekit]"
pip install livekit-plugins-openai livekit-plugins-deepgram livekit-plugins-cartesia
```
</CodeGroup>

For Pipecat, install the Pipecat service extras you need directly from Pipecat
(for example `pip install "pipecat-ai[openai,deepgram,cartesia]"`) alongside
`voicegateway[pipecat]`. VoiceGateway wraps and observes the native Pipecat
services you already configure; it does not re-home their keys.

## The core stays pure

Because the core imports no framework, you can `import voicegateway` in a
process that has neither installed (a CLI, a collector, a test rig) and it works.
`attach()` and `guard()` only reach for a framework when you call them with a
target of that framework. If the matching extra is missing they raise a clear,
actionable error:

```
ImportError: voicegateway[pipecat] is required for this operation.
Install it with: pip install voicegateway[pipecat]
```

You can verify the purity yourself:

```python
import voicegateway, sys
assert "livekit" not in sys.modules
assert "pipecat" not in sys.modules
```

## The two seams work the same on both

The public surface is identical across frameworks. You pass a LiveKit
`AgentSession` or a Pipecat `PipelineTask` to `attach()`, and a native LiveKit
plugin or a native Pipecat service to `guard()`. VoiceGateway routes to the right
implementation for you.

| Seam | Role | LiveKit target | Pipecat target |
|---|---|---|---|
| [`attach()`](/guide/attach) | observe (passive) | `AgentSession` | `PipelineTask` |
| [`guard()`](/guide/guard) | control (active) | native plugin | native service |

