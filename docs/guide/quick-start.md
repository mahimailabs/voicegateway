# Quick Start

Get VoiceGateway running in 5 minutes. By the end of this guide you will have a working Python script that routes STT, LLM, and TTS requests through the gateway.

## Prerequisites

- Python 3.11 or later
- An API key for at least one cloud provider (e.g., Deepgram, OpenAI)

## 1. Install VoiceGateway

```bash
pip install voicegateway[cloud]
```

This installs VoiceGateway along with all cloud provider SDKs. For a minimal install, see [Installation](/guide/installation).

## 2. Generate a config file

```bash
voicegw init
```

This creates a `voicegw.yaml` in your current directory with a commented-out template.

<!-- TODO: screenshot of voicegw init output -->

## 3. Add your API keys

Open `voicegw.yaml` and add at least one provider. For this quick start we will use Deepgram for STT and OpenAI for LLM and TTS:

```yaml
providers:
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}
  openai:
    api_key: ${OPENAI_API_KEY}

cost_tracking:
  enabled: true
```

Then export your keys:

```bash
export DEEPGRAM_API_KEY="your-deepgram-key"
export OPENAI_API_KEY="your-openai-key"
```

## 4. Write a Python script

Create a file called `demo.py`:

```python
from voicegateway import inference

# Create model instances. AgentSession would consume them directly;
# here we print them to confirm they are wired LiveKit plugins.
stt = inference.STT("deepgram/nova-3")
llm = inference.LLM("openai/gpt-4.1-mini")
tts = inference.TTS("openai/tts-1")

print("STT:", stt)
print("LLM:", llm)
print("TTS:", tts)
```

## 5. Run it

```bash
python demo.py
```

<!-- TODO: screenshot of demo.py output -->

You should see the instantiated provider objects printed. VoiceGateway resolved the `provider/model` strings, loaded the correct SDKs, and wrapped each instance with cost tracking and latency monitoring middleware.

## 6. Check provider status

```bash
voicegw status
```

<!-- TODO: screenshot of voicegw status output -->

This shows all configured providers and their current status.

## 7. View costs

```bash
voicegw costs
```

After running some requests through the gateway, this command shows your cost breakdown by provider and model.

## Routing per project

Once you start running multiple agents, give each its own project entry in `voicegw.yaml` so cost rows and provider keys stay separated:

```yaml
projects:
  my-agent:
    name: My First Agent
    daily_budget: 5.00
    providers:
      deepgram:
        api_key: ${MY_AGENT_DEEPGRAM_KEY}
      openai:
        api_key: ${MY_AGENT_OPENAI_KEY}

default_project: my-agent
```

The inference factories pick the project up automatically. Override per-context with `inference.set_project("my-agent")` when you need to.

## Adding fallbacks

Manual startup-walk pattern (resolver-time fallback) with a chain in `voicegw.yaml`:

```yaml
fallbacks:
  stt: [deepgram/nova-3, openai/whisper-1]
  llm: [openai/gpt-4.1-mini, anthropic/claude-sonnet-4-20250514]
```

See [`examples/fallback_agent.py`](https://github.com/mahimailabs/voicegateway/blob/main/examples/fallback_agent.py) for the worked code. Once `AgentSession` starts, the resolved model is used for the whole call. v0.0.6 will add a first-class `fallback=` parameter to the `inference` factories.

## Next steps

- [Installation](/guide/installation) -- all install variants and Docker setup
- [First Agent](/guide/first-agent) -- build a full voice agent with LiveKit Agents
- [Core Concepts](/guide/core-concepts) -- understand the key abstractions
- [Configuration Reference](/configuration/voicegw-yaml) -- complete YAML reference
