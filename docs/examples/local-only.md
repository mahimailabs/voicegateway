# Local-Only Deployment

Run VoiceGateway entirely on local hardware with zero cloud dependencies. Uses Ollama for LLM, Whisper for STT, and Kokoro for TTS. Ideal for air-gapped environments, development without API keys, or privacy-sensitive deployments.

## Prerequisites

### Install Ollama

```bash
# macOS / Linux
curl -fsSL https://ollama.com/install.sh | sh

# Pull a model
ollama pull qwen2.5:3b
```

### Install VoiceGateway with Local Providers

```bash
pip install voicegateway[whisper,kokoro]
```

Whisper requires `torch` and will download model weights on first use. Kokoro requires the `kokoro` package.

## Configuration

Create `voicegw.yaml`:

```yaml
providers:
  ollama:
    base_url: http://localhost:11434
  whisper: {}
  kokoro: {}

models:
  stt:
    local/whisper-large-v3:
      provider: whisper
      model: large-v3
    local/whisper-base:
      provider: whisper
      model: base
  llm:
    ollama/qwen2.5:3b:
      provider: ollama
      model: qwen2.5:3b
    ollama/llama3.2:1b:
      provider: ollama
      model: llama3.2:1b
  tts:
    local/kokoro:
      provider: kokoro

stacks:
  local:
    stt: local/whisper-large-v3
    llm: ollama/qwen2.5:3b
    tts: local/kokoro
  fast:
    stt: local/whisper-base
    llm: ollama/llama3.2:1b
    tts: local/kokoro

fallbacks:
  stt:
    - local/whisper-large-v3
    - local/whisper-base
  llm:
    - ollama/qwen2.5:3b
    - ollama/llama3.2:1b

projects:
  local-dev:
    name: Local Development
    daily_budget: 0  # Unlimited (local models are free)
    tags: [development, local]

default_project: local-dev

cost_tracking:
  enabled: true  # Still tracks requests, costs will be $0.00

observability:
  latency_tracking: true
```

## Basic Usage

```python
from voicegateway import inference

# default_project: local-dev in voicegw.yaml means the inference
# factories pick up local-dev automatically. All local, no API keys.
stt = inference.STT("local/whisper-large-v3")
llm = inference.LLM("ollama/qwen2.5:3b")
tts = inference.TTS("local/kokoro")
```

## LiveKit Agent with Local Models

```python
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import silero
from voicegateway import inference


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=inference.STT("local/whisper-large-v3"),
        llm=inference.LLM("ollama/qwen2.5:3b"),
        tts=inference.TTS("local/kokoro"),
    )

    await session.start(
        agent=Agent(
            instructions=(
                "You are a helpful voice assistant running entirely on local hardware. "
                "Be concise: local models work best with shorter responses."
            ),
        ),
        room=ctx.room,
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
```

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

  dashboard:
    build:
      context: .
      dockerfile: src/dashboard/Dockerfile
    container_name: voicegateway-dash
    ports:
      - "9090:9090"
    volumes:
      - voicegw-data:/data:ro
    environment:
      - VOICEGW_API_URL=http://voicegateway:8080
      - VOICEGW_DB_PATH=/data/voicegw.db
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

## Using Piper TTS as an Alternative

If Kokoro is not available, Piper is another local TTS option:

```yaml
providers:
  piper: {}

models:
  tts:
    local/piper:
      provider: piper
      default_voice: en_US-lessac-medium
```

```bash
pip install voicegateway[piper]
```

## Performance Considerations

Local models have different performance characteristics than cloud APIs:

| Metric | Cloud (Deepgram + GPT-4.1) | Local (Whisper + Qwen2.5) |
|--------|---------------------------|---------------------------|
| STT TTFB | ~100-200ms | ~500-2000ms (depends on GPU) |
| LLM TTFB | ~200-500ms | ~300-3000ms (depends on model size) |
| TTS TTFB | ~100-300ms | ~200-1000ms |
| Cost | ~$0.01-0.05/request | $0.00 |

Tips for optimizing local performance:

- **GPU acceleration:** ensure CUDA/Metal is available for Whisper and Ollama
- **Smaller models:** use `local/whisper-base` instead of `local/whisper-large-v3` for faster STT
- **Quantized LLMs:** Ollama automatically uses quantized models (Q4_0, Q4_K_M)
- **Keep models warm:** Ollama keeps the most recent model in memory; avoid switching frequently

## Hybrid: Local Fallback for Cloud

A common pattern is to use cloud providers normally but fall back to local models when they are unavailable or the budget is exceeded:

```yaml
fallbacks:
  stt:
    - deepgram/nova-3
    - local/whisper-large-v3
  llm:
    - openai/gpt-4.1-mini
    - ollama/qwen2.5:3b
  tts:
    - cartesia/sonic-3
    - local/kokoro

projects:
  prod:
    daily_budget: 50.00
    budget_action: throttle  # Falls back to local on exceed
```

See [Fallback Chains](./fallback-chains) and [Budget Enforcement](./budget-enforcement) for more details.
