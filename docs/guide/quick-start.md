---
title: Quick start
description: Get VoiceGateway running in 5 minutes. Daemon up, dashboard open, one Python script proves the inference factories resolve correctly.
---

# Quick start

By the end of this guide you have a running daemon, an open
dashboard, and a Python script that exercises the inference
factories so you can confirm provider keys resolve and costs land
in the dashboard.

## Prerequisites

- Python 3.11 or later
- An API key for at least one cloud provider (Deepgram, OpenAI,
  Anthropic, Groq, Cartesia, ElevenLabs, or AssemblyAI)

## 1. Install

```bash
pipx install 'voicegateway[cloud,dashboard]'
```

Or if you prefer uv:

```bash
uv tool install 'voicegateway[cloud,dashboard]'
```

The `cloud` extra pulls every cloud provider SDK; the `dashboard`
extra ships the prebuilt React bundle and the dashboard endpoints.
For a minimal install of one provider only, see
[Installation](/guide/installation).

## 2. Onboard

```bash
voicegw onboard
```

Five questions, four with working defaults (press Enter to accept):

1. Project name (default: `default`).
2. Provider (default: `openai`).
3. API key (no default; paste yours).
4. Port (default: `8080`).
5. Install daemon? (default: yes).

The wizard writes `~/.config/voicegateway/voicegw.yaml`, registers
the daemon with your OS service manager (LaunchAgent on macOS,
`systemd --user` on Linux, Scheduled Task on Windows), and starts
it.

## 3. Open the dashboard

```bash
voicegw dashboard
```

That opens your browser at the daemon URL (default
`http://127.0.0.1:8080`). The daemon serves the React UI at `/`,
the dashboard API at `/api/*`, and the public HTTP API at `/v1/*`
on the same port.

## 4. Verify the inference factories

Create `demo.py`:

```python
from voicegateway.inference import STT, LLM, TTS

# Each call resolves provider/model -> loads the SDK -> wraps with
# cost-tracking and latency middleware. AgentSession would consume
# them directly; here we print to confirm wiring.
stt = STT("openai/whisper-1")
llm = LLM("openai/gpt-4.1-mini")
tts = TTS("openai/tts-1")

print("STT:", stt)
print("LLM:", llm)
print("TTS:", tts)
```

Run it:

```bash
python demo.py
```

You should see three instantiated provider objects. VoiceGateway
resolved the `provider/model` strings, loaded the correct SDKs, and
wrapped each instance with cost-tracking and latency middleware.

## 5. See costs in the dashboard

Trigger one call (any request that uses the inference factories
above) and refresh the dashboard at `http://127.0.0.1:8080/costs`.
The row shows the model, provider, modality, and the per-call cost
in USD with the pricing source attribution (`voice-prices@<version>`
for cloud models, `voicegateway-local` for self-hosted).

In the terminal:

```bash
voicegw costs
voicegw status
voicegw logs
```

## Add a project

Multiple agents share one daemon? Give each its own project entry
in `voicegw.yaml` so cost rows and provider keys stay separated:

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

The inference factories pick the project up automatically. Override
per-context with `set_project("my-agent")` from
`voicegateway.core.active_project` when you need to.

## Add fallbacks

Resolver-time fallback chain in `voicegw.yaml`:

```yaml
fallbacks:
  stt: [deepgram/nova-3, openai/whisper-1]
  llm: [openai/gpt-4.1-mini, anthropic/claude-sonnet-4-5]
  tts: [openai/tts-1, elevenlabs/eleven_turbo_v2_5]
```

Walk the chain at startup by trying each model with
`STT/LLM/TTS(model_id)` and using the first one whose provider
plugin imports cleanly. Once `AgentSession` starts, the resolved
model is used for the whole call.

## Next steps

- [Installation](/guide/installation): all install variants
  (curl-bash, pipx, uv, Docker).
- [First agent](/guide/first-agent): wire VoiceGateway into a
  full LiveKit voice agent.
- [Core concepts](/guide/core-concepts): understand the
  abstractions (modality, provider, project, stack).
- [Configuration reference](/configuration/voicegw-yaml): every
  YAML key.
