---
title: Installation
description: Install VoiceGateway with uv or pip. Pick the framework extra for LiveKit or Pipecat, then bring your own provider plugins. Python 3.11+ required.
---

# Installation

## Requirements

- Python 3.11 or later
- macOS, Linux, or WSL on Windows

## Framework extras

Install the extra that matches your agent framework. The core package is
framework-neutral: `import voicegateway` imports neither LiveKit Agents nor
Pipecat.

<CodeGroup>
```bash uv
# LiveKit Agents
uv pip install "voicegateway[livekit]"

# Pipecat
uv pip install "voicegateway[pipecat]"
```
```bash pip
# LiveKit Agents
pip install "voicegateway[livekit]"

# Pipecat
pip install "voicegateway[pipecat]"
```
</CodeGroup>

## Provider plugins (LiveKit)

VoiceGateway is framework-agnostic and does not bundle provider wheels. You
install the LiveKit provider plugins your agent uses, exactly as you would
without VoiceGateway (you likely already have them). VoiceGateway meters those
native instances by `model_id` through `voice-prices`.

<CodeGroup>
```bash uv
uv pip install "voicegateway[livekit]"

# One provider
uv pip install livekit-plugins-openai

# Several at once
uv pip install livekit-plugins-openai livekit-plugins-deepgram livekit-plugins-cartesia
```
```bash pip
pip install "voicegateway[livekit]"

pip install livekit-plugins-openai

pip install livekit-plugins-openai livekit-plugins-deepgram livekit-plugins-cartesia
```
</CodeGroup>

| Provider | LiveKit plugin wheel |
|---|---|
| OpenAI | `livekit-plugins-openai` |
| Deepgram | `livekit-plugins-deepgram` |
| Anthropic | `livekit-plugins-anthropic` |
| Groq | `livekit-plugins-openai` |
| Cartesia | `livekit-plugins-cartesia` |
| ElevenLabs | `livekit-plugins-elevenlabs` |
| AssemblyAI | `livekit-plugins-assemblyai` |

<Note>
  `attach()` and `guard()` error messages point at the upstream wheel (for
  example `livekit-plugins-openai`), not a VoiceGateway extra. Install the wheel
  named in the error into your agent environment.
</Note>

## Provider extras (Pipecat)

For Pipecat, install `voicegateway[pipecat]` and then install provider service
extras directly from Pipecat. VoiceGateway wraps the native Pipecat services you
already configure.

<CodeGroup>
```bash uv
uv pip install "voicegateway[pipecat]"
uv pip install "pipecat-ai[openai,deepgram,cartesia]"
```
```bash pip
pip install "voicegateway[pipecat]"
pip install "pipecat-ai[openai,deepgram,cartesia]"
```
</CodeGroup>

## Additional extras

VoiceGateway ships exactly five extras.

| Extra | What it adds |
|---|---|
| `livekit` | LiveKit Agents seam for `attach()` / `guard()` |
| `pipecat` | Pipecat seam for `attach()` / `guard()` |
| `dashboard` | Prebuilt React dashboard bundle (`voicegw dashboard`) plus the HTTP API server |
| `mcp` | MCP server for IDE integration |
| `collector` | Self-hosted fleet collector (Postgres + DuckDB backend) |

There are no per-provider or local-model extras. VoiceGateway meters native
provider instances and `local/*` and `ollama/*` model ids for free by
`model_id`, so you bring the provider plugins and local runtimes yourself. For
local models install the runtime directly: Whisper with
`pip install faster-whisper`, Kokoro with `pip install kokoro-onnx onnxruntime`,
Piper with `pip install piper-tts`.

You can combine any extras. To install the everything set:

<CodeGroup>
```bash uv
uv pip install "voicegateway[collector,livekit,pipecat]"
```
```bash pip
pip install "voicegateway[collector,livekit,pipecat]"
```
</CodeGroup>

## Install from source

```bash
git clone https://github.com/mahimailabs/voicegateway.git
cd voicegateway
pip install -e ".[dev]"
```

The `dev` extra includes pytest, ruff, and mypy. To build the dashboard
frontend from source:

```bash
cd src/dashboard/frontend
npm install
npm run build
```

## Docker

```bash
# HTTP API + dashboard on port 8080
docker compose up -d

# Plus Ollama for local LLM
docker compose --profile local up -d
```

Mount your config and pass provider keys as environment variables:

```bash
docker compose up -d \
  -e DEEPGRAM_API_KEY=your-key \
  -e OPENAI_API_KEY=your-key \
  -v ./voicegw.yaml:/app/voicegw.yaml
```

## Verify

```bash
voicegw --version
voicegw status
```

If `voicegw` is not on your PATH after a `uv pip install`, activate the
environment or use `python -m voicegateway.cli --version`.

## Upgrading

<CodeGroup>
```bash uv
uv pip install --upgrade voicegateway
```
```bash pip
pip install --upgrade voicegateway
```
</CodeGroup>

After upgrading, check for config schema changes:

```bash
voicegw init --diff
```

## Troubleshooting

**`ModuleNotFoundError: No module named 'deepgram'`**

Your agent is missing the Deepgram plugin. VoiceGateway does not install provider
wheels, so install the one your agent uses:

```bash
pip install livekit-plugins-deepgram
```

**`ConfigError: No voicegw.yaml found`**

VoiceGateway searches in this order:

1. `./voicegw.yaml` (current directory)
2. `~/.config/voicegateway/voicegw.yaml`
3. `/etc/voicegateway/voicegw.yaml`

Set `VOICEGW_CONFIG` to an explicit path, or run `voicegw init` to generate a
starter config.

## Next steps

- [Quickstart](/get-started): five-minute path from install to first cost row.
- [First agent](/guide/first-agent): a complete worked agent with `attach()` and `guard()`.
- [Frameworks and extras](/guide/frameworks): framework-neutral core explained.
