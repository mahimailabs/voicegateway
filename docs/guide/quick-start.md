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
from voicegateway import Gateway

gw = Gateway()

# Create model instances
stt = gw.stt("deepgram/nova-3")
llm = gw.llm("openai/gpt-4.1-mini")
tts = gw.tts("openai/tts-1")

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

## Using stacks

Instead of specifying individual models, you can define named stacks in your config:

```yaml
stacks:
  premium:
    stt: deepgram/nova-3
    llm: openai/gpt-4.1-mini
    tts: openai/tts-1
```

Then resolve all three at once:

```python
stt, llm, tts = gw.stack("premium")
```

## Adding fallbacks

Add a fallback chain so the gateway automatically tries the next provider if one fails:

```yaml
fallbacks:
  stt:
    - deepgram/nova-3
    - openai/whisper-1
  llm:
    - openai/gpt-4.1-mini
    - anthropic/claude-sonnet-4-20250514
```

```python
stt = gw.stt_with_fallback()
llm = gw.llm_with_fallback()
```

## Next steps

- [Installation](/guide/installation) -- all install variants and Docker setup
- [First Agent](/guide/first-agent) -- build a full voice agent with LiveKit Agents
- [Core Concepts](/guide/core-concepts) -- understand the key abstractions
- [Configuration Reference](/configuration/voicegw-yaml) -- complete YAML reference
