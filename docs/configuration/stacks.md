# Stacks

Stacks are named bundles that map a single name to one STT model, one LLM model, and one TTS model. They let you define quality/cost tiers and switch between them with a single string.

## Defining stacks

Stacks are defined in `voicegw.yaml` under the `stacks` section. Each stack has up to three keys: `stt`, `llm`, and `tts`. All three are optional -- you can define a stack with only the modalities you need.

```yaml
stacks:
  premium:
    stt: deepgram/nova-3
    llm: anthropic/claude-sonnet-4-20250514
    tts: cartesia/sonic-3
  budget:
    stt: groq/whisper-large-v3
    llm: groq/llama-3.3-70b-versatile
    tts: piper/en_US-lessac-medium
  local:
    stt: whisper/large-v3
    llm: ollama/llama3
    tts: kokoro/default
```

## Using stacks from code

The `Gateway.stack()` method resolves a stack name into an `(stt, llm, tts)` tuple:

```python
from voicegateway import Gateway

gw = Gateway()

# Resolve the "premium" stack
stt, llm, tts = gw.stack("premium")

# Use with a project for cost tracking
stt, llm, tts = gw.stack("premium", project="customer-support")
```

If a stack does not define a particular modality, the corresponding value in the tuple is `None`:

```yaml
stacks:
  llm-only:
    llm: openai/gpt-4.1-mini
```

```python
stt, llm, tts = gw.stack("llm-only")
# stt is None, tts is None
```

## Using stacks with projects

Projects can specify a `default_stack` that determines which models are used when requests are made through that project:

```yaml
projects:
  production:
    name: Production
    default_stack: premium
    daily_budget: 100.00
    budget_action: throttle
  development:
    name: Development
    default_stack: local
    daily_budget: 5.00
    budget_action: warn
```

## When to use stacks vs. individual models

- **Use stacks** when you have well-defined quality/cost tiers and want to switch all three modalities together.
- **Use individual models** (e.g., `gw.stt("deepgram/nova-3")`) when you need fine-grained control over each modality independently.
- **Use fallback chains** when you want automatic failover rather than a fixed model selection.

## Validation

The Pydantic schema validates stack definitions at config load time. If you reference a stack name that does not exist, `Gateway.stack()` raises a `ValueError` listing the available stacks.

See: [Projects](/configuration/projects), [Models](/configuration/models), [voicegw.yaml Reference](/configuration/voicegw-yaml)
