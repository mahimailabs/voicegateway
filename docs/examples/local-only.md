---
title: Local-Only Deployment
description: Run a voice agent with zero cloud dependencies using Whisper for STT, Ollama for LLM, and Kokoro for TTS.
---

# Local-Only Deployment

Run VoiceGateway entirely on local hardware with zero cloud dependencies. Uses Ollama for LLM, Whisper for STT, and Kokoro for TTS. Ideal for air-gapped environments, development without API keys, or privacy-sensitive deployments.

## Prerequisites

<Steps>
  <Step title="Install Ollama">
```bash
# macOS / Linux
curl -fsSL https://ollama.com/install.sh | sh

# Pull a model
ollama pull qwen2.5:3b
```
  </Step>
  <Step title="Install VoiceGateway with local providers">
<CodeGroup>
```bash uv
uv add "voicegateway[whisper,kokoro]"
```

```bash pip
pip install "voicegateway[whisper,kokoro]"
```
</CodeGroup>

Whisper requires `torch` and downloads model weights on first use. Kokoro requires the `kokoro` package.
  </Step>
  <Step title="Create voicegw.yaml">
```yaml
providers:
  ollama:
    base_url: http://localhost:11434
  whisper: {}
  kokoro: {}

projects:
  local-dev:
    name: Local Development
    daily_budget: 0   # local models are free
    tags: [development, local]

default_project: local-dev

cost_tracking:
  enabled: true   # still records requests; costs will be $0.00

observability:
  latency_tracking: true
```
  </Step>
</Steps>

## Agent code

<Tabs>
  <Tab title="LiveKit">
```python
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import silero
from voicegateway import attach

# Assumes voicegateway local providers are wired in voicegw.yaml.
# The local Whisper and Kokoro providers expose the same plugin interface
# as their cloud counterparts.
from voicegateway.providers.whisper import WhisperSTT
from voicegateway.providers.kokoro import KokoroTTS
from voicegateway.providers.ollama import OllamaLLM


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=WhisperSTT(model="large-v3"),
        llm=OllamaLLM(model="qwen2.5:3b"),
        tts=KokoroTTS(),
    )

    attach(session, project="local-dev")

    await session.start(
        agent=Agent(
            instructions=(
                "You are a helpful voice assistant running on local hardware. "
                "Be concise: local models work best with shorter responses."
            ),
        ),
        room=ctx.room,
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
```
  </Tab>
  <Tab title="Pipecat">
```python
import voicegateway
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams, PipelineTask

# Import local provider services (registered via voicegateway providers).
from voicegateway.providers.whisper import WhisperSTTService
from voicegateway.providers.kokoro import KokoroTTSService
from voicegateway.providers.ollama import OllamaLLMService


def build_task(transport_input, transport_output):
    stt = WhisperSTTService(model="large-v3")
    llm = OllamaLLMService(model="qwen2.5:3b")
    tts = KokoroTTSService()

    pipeline = Pipeline([transport_input, stt, llm, tts, transport_output])

    return PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[voicegateway.Observer(project="local-dev")],
    )
```
  </Tab>
</Tabs>

## Docker Compose with Ollama

For a containerized local-only setup:

```yaml
version: "3.8"

services:
  voicegateway:
    build:
      context: .
      dockerfile: src/voicegateway/Dockerfile
    container_name: voicegateway
    ports:
      - "8080:8080"
    volumes:
      - voicegw-data:/data
      - ./voicegw.yaml:/app/voicegw.yaml:ro
    environment:
      - VOICEGW_CONFIG=/app/voicegw.yaml
      - VOICEGW_DB_PATH=/data/voicegw.db
    depends_on:
      - ollama
    networks:
      - voicegw-net

  ollama:
    image: ollama/ollama:latest
    container_name: voicegateway-ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama-models:/root/.ollama
    networks:
      - voicegw-net

volumes:
  voicegw-data:
  ollama-models:

networks:
  voicegw-net:
```

Update `voicegw.yaml` to point Ollama at the container:

```yaml
providers:
  ollama:
    base_url: http://ollama:11434
```

Then start and pull the model:

```bash
docker compose up -d
docker exec voicegateway-ollama ollama pull qwen2.5:3b
```

## Using Piper TTS as an alternative

If Kokoro is not available, Piper is another local TTS option:

<CodeGroup>
```bash uv
uv add "voicegateway[piper]"
```

```bash pip
pip install "voicegateway[piper]"
```
</CodeGroup>

```yaml
providers:
  piper: {}
```

## Performance considerations

Local models have different performance characteristics than cloud APIs:

| Metric | Cloud (Deepgram + GPT-4.1) | Local (Whisper + Qwen2.5) |
|--------|---------------------------|---------------------------|
| STT TTFB | ~100-200ms | ~500-2000ms (depends on GPU) |
| LLM TTFB | ~200-500ms | ~300-3000ms (depends on model size) |
| TTS TTFB | ~100-300ms | ~200-1000ms |
| Cost | ~$0.01-0.05/request | $0.00 |

Tips for optimizing local performance:

- **GPU acceleration:** ensure CUDA/Metal is available for Whisper and Ollama.
- **Smaller models:** use `whisper-base` instead of `large-v3` for faster STT.
- **Quantized LLMs:** Ollama automatically uses quantized models (Q4_0, Q4_K_M).
- **Keep models warm:** Ollama keeps the most recent model in memory; avoid switching frequently.

## Hybrid: local fallback for cloud

A common pattern is cloud providers as primaries, local as the final fallback:

```python
from livekit.plugins import deepgram, openai
from voicegateway import guard
from voicegateway.providers.whisper import WhisperSTT

stt = guard(
    deepgram.STT(model="nova-3"),
    fallback=[WhisperSTT(model="large-v3")],
    project="prod",
)
```

See [Fallback Chains](/examples/fallback-chains) and [Budget Enforcement](/examples/budget-enforcement) for more details.
