---
title: Stacks
description: Named YAML bundles that map a single name to one STT, one LLM, and one TTS model, used as a documentation and dashboard hint for VoiceGateway projects.
---

# Stacks

Stacks are named YAML bundles that map a single name to one STT model, one LLM model, and one TTS model. They are a documentation and dashboard hint only: the `voicegateway.inference` module does not consume them. The dashboard uses `default_stack` on a project to render a recommended-stack badge; the rest of the gateway ignores the field.

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

A project can carry an optional `default_stack` that points at one of these names:

```yaml
projects:
  production:
    name: Production
    default_stack: premium
    daily_budget: 100.00
    budget_action: throttle
```

The dashboard's project page renders the stack name next to the project. No code reads the stack to construct factories: pick the model id you want and pass it to `inference.STT/LLM/TTS` directly.

## Using stacks from code

```python
from voicegateway.inference import STT, LLM, TTS

# Construct each modality explicitly with the model id from the
# stack you want. There is no Gateway.stack(...) helper.
stt = STT("deepgram/nova-3")
llm = LLM("anthropic/claude-sonnet-4-5")
tts = TTS("cartesia/sonic-3")
```

If you find yourself repeating the same triple across agents, define a small Python helper in your own code that returns the three calls. The gateway does not need a built-in for it.

## Roadmap

A future release may bring back a one-line `inference.stack("premium")` shortcut once the trade-offs (per-project key resolution, fallback chains, model-id validation) are sorted. For now stacks live in YAML for dashboard display and team-internal documentation.

See: [Projects](/configuration/projects), [Models](/configuration/models), [voicegw.yaml Reference](/configuration/voicegw-yaml)
