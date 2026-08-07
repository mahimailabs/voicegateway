---
title: Local-Only Deployment
description: Run a voice agent's LLM against Ollama for zero API cost. Local STT and TTS need your own framework-compatible wrapper around a local runtime.
---
Zero cloud dependencies for the LLM: point a native OpenAI-compatible plugin at
a locally running Ollama server instead of a cloud LLM. `attach()` and
`guard()` work exactly the same as with any cloud provider.

## Prerequisites

```bash
# macOS / Linux
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:3b
```

Install VoiceGateway for your framework as usual (`pip install
"voicegateway[livekit]"` or `[pipecat]`). VoiceGateway does not bundle
local-model runtimes; see [Installation](/guide/installation) for the
`faster-whisper` / `kokoro-onnx` / `piper-tts` install commands if you also
want local STT or TTS.

## Local LLM: Ollama via the OpenAI plugin

Ollama serves an OpenAI-compatible API, so there is no separate Ollama plugin
to install. Point `livekit.plugins.openai.LLM` (or
`pipecat.services.openai.llm.OpenAILLMService`) at it with a custom client:

```python
import httpx
from livekit.plugins import openai
from openai import AsyncOpenAI

ollama_client = AsyncOpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama",  # unused by Ollama, required by the client constructor
    http_client=httpx.AsyncClient(timeout=120.0),
)
llm = openai.LLM(model="qwen2.5:3b", client=ollama_client)
```

Wrap it in `guard()` and pass the session to `attach()` exactly as with a
cloud provider (see [First agent](/guide/first-agent)):

```python
import voicegateway

guarded_llm = voicegateway.guard(llm, project="local-dev")
session = AgentSession(stt=deepgram.STT(model="nova-3"), llm=guarded_llm, tts=cartesia.TTS(model="sonic-3"))
voicegateway.attach(session, project="local-dev")
```

<Note>
`attach()` identifies a component's provider from its Python module path, so
an `openai.LLM` instance is always tagged `openai/<model>`, even when its
client points at Ollama. Ollama has no billing either way, but the cost row
lands unpriced under `openai/qwen2.5:3b` rather than as a clean `ollama/` $0
row. This is a cosmetic labeling gap, not a cost bug.
</Note>

## Local STT and TTS

VoiceGateway ships no LiveKit- or Pipecat-compatible Whisper or Kokoro plugin.
The `whisper` / `kokoro` / `piper` blocks in `voicegw.yaml` (see
[Providers](/configuration/providers)) back `voicegw status` and the
dashboard's connection check only; they do not construct STT or TTS objects
for your session.

To run STT or TTS locally, install the runtime yourself (`faster-whisper`,
`kokoro-onnx`, or `piper-tts`) and wrap it in a class that satisfies your
framework's STT/TTS interface (`livekit.agents.stt.STT` / `tts.TTS`, or
Pipecat's `STTService` / `TTSService`). Tag the model id `local/<name>` so
`attach()` prices it at $0 and the dashboard groups it with the other local
rows.

Local STT and TTS trade cost for latency: expect STT and TTS first-byte times
in the seconds, not milliseconds, without a GPU. For most voice agents, cloud
STT/TTS paired with a local LLM is the better cost/latency tradeoff; going
fully local is worth it mainly for air-gapped or privacy-constrained
deployments, where the LLM is usually the largest line item anyway.

## Related

- [First agent](/guide/first-agent): the full LiveKit agent this pattern drops into.
- [guard()](/guide/guard): the fallback, rate-limit, and budget signature.
- [Providers](/configuration/providers): every `voicegw.yaml` provider block, including the local ones.
