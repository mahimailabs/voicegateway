---
title: Frameworks and extras
description: VoiceGateway's core is framework-neutral. Pick the extra for the agent framework you run (LiveKit or Pipecat); import voicegateway pulls neither.
---

VoiceGateway's core is framework-neutral. A bare `import voicegateway`
imports neither [LiveKit Agents](https://docs.livekit.io/agents/) nor
[Pipecat](https://docs.pipecat.ai/). The record model, `voice-prices`
pricing, the storage sinks, and the cost tracker have no framework
dependency.

`attach()` and `guard()` are the only two seams that touch a framework. Each
detects the framework of the object you hand it by inspecting `__module__`
strings, without importing anything, then lazily imports the matching
implementation on first use:

| Seam | LiveKit target | Pipecat target |
|---|---|---|
| [`attach()`](/guide/attach) | `AgentSession` | `PipelineTask` |
| [`guard()`](/guide/guard) | native LiveKit plugin | native Pipecat service |

Detection also walks the MRO, so a subclass of a framework base class
defined in your own code (e.g. a custom `livekit.agents.llm.LLM` subclass)
still resolves correctly.

## Install only what you run

Install `voicegateway[livekit]` or `voicegateway[pipecat]`, whichever your
agent runs. See [Installation](/guide/installation) for the full extras
matrix, provider-plugin wheels, and Docker. You bring your own provider
plugins (`livekit-plugins-*`, or Pipecat's own service extras);
VoiceGateway never needs them installed itself, it meters the native
instances you pass to `attach()` / `guard()` by `model_id` through
`voice-prices`.

## Missing extra

Calling `attach()` or `guard()` with a target whose framework extra isn't
installed raises a clear, actionable error instead of a raw
`ModuleNotFoundError`:

```text
ImportError: voicegateway[pipecat] is required for this operation.
Install it with: pip install voicegateway[pipecat]
```

## The core stays pure

Because the core imports no framework, `import voicegateway` works in a
process that has neither installed: a CLI, a collector, a test rig.
`attach()` and `guard()` only reach for a framework when you call them with a
target of that framework. Verify it yourself:

```python
import voicegateway, sys
assert "livekit" not in sys.modules
assert "pipecat" not in sys.modules
```

## See also

- [attach()](/guide/attach): the observe seam.
- [guard()](/guide/guard): the control seam.
- [Installation](/guide/installation): the full extras matrix, provider
  plugins, and Docker.
