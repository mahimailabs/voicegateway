---
title: Stacks
description: Named YAML bundles that map one name to an STT, LLM, and TTS model ID, used as quality-tier presets for the dashboard and team documentation.
---

# Stacks

Stacks are named YAML bundles that map one name to an STT model ID, one LLM model ID, and one TTS model ID. They serve as quality-tier presets: you reference a stack name on a project, and the dashboard renders it as a recommended-configuration badge.

<Note>
Stacks are a dashboard and documentation hint. Wire your native LiveKit or Pipecat providers directly using `attach()` and pass the model ID you want. No code reads `default_stack` at runtime to construct providers.
</Note>

## Defining stacks

```yaml
stacks:
  premium:
    stt: deepgram/nova-3
    llm: anthropic/claude-sonnet-4-5
    tts: cartesia/sonic-3
  budget:
    stt: groq/whisper-large-v3
    llm: groq/llama-3.3-70b-versatile
    tts: local/piper:en_US-lessac-medium
  local:
    stt: local/whisper-large-v3
    llm: ollama/llama3.2:3b
    tts: local/kokoro
```

Each stack entry has exactly three keys: `stt`, `llm`, and `tts`. Values are `provider/model` strings in the standard format.

## Referencing a stack from a project

Add `default_stack` to any project. The value must match a key under `stacks:`.

```yaml
stacks:
  premium:
    stt: deepgram/nova-3
    llm: anthropic/claude-sonnet-4-5
    tts: cartesia/sonic-3

projects:
  production:
    name: Production
    default_stack: premium
    daily_budget: 100.00
    budget_action: throttle
```

The dashboard project page renders the stack name alongside the project. No additional runtime behavior is attached to this field.

## Wiring providers in agent code

Pick the model IDs from your chosen stack and pass them to your framework's native provider constructors. Wrap with `attach()` to meter cost.

<Tabs>
  <Tab title="LiveKit">
```python
from livekit.agents import Agent, AgentSession
from livekit.plugins import deepgram, anthropic, cartesia
from voicegateway import attach

session = AgentSession(
    stt=attach(deepgram.STT(model="nova-3")),
    llm=attach(anthropic.LLM(model="claude-sonnet-4-5")),
    tts=attach(cartesia.TTS(model="sonic-3")),
)
```
  </Tab>
  <Tab title="Pipecat">
```python
from pipecat.services.deepgram import DeepgramSTTService
from pipecat.services.anthropic import AnthropicLLMService
from pipecat.services.cartesia import CartesiaTTSService
from voicegateway import attach

stt = attach(DeepgramSTTService(model="nova-3"))
llm = attach(AnthropicLLMService(model="claude-sonnet-4-5"))
tts = attach(CartesiaTTSService(model="sonic-3"))
```
  </Tab>
</Tabs>

If you find yourself repeating the same triple across agents, define a small helper in your own code that returns the three attached providers. See [attach()](/guide/attach) for the full API.

---

See [Projects](/configuration/projects) for the `default_stack` field and budget configuration.
See [Models](/configuration/models) for the `provider/model` string format.
See [voicegw.yaml reference](/configuration/voicegw-yaml) for the full config file shape.
