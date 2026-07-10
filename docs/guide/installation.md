---
title: Installation
description: Install VoiceGateway with uv or pip. Pick the framework extra for LiveKit or Pipecat, then add provider extras for the SDKs you need. Python 3.11+ required.
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

## Provider extras (LiveKit)

Provider extras imply `livekit`. A single line pulls the core, the LiveKit
adapter, and the provider SDK you name.

<CodeGroup>
```bash uv
# One provider
uv pip install "voicegateway[openai]"

# Several at once
uv pip install "voicegateway[openai,deepgram,cartesia]"
```
```bash pip
pip install "voicegateway[openai]"

pip install "voicegateway[openai,deepgram,cartesia]"
```
</CodeGroup>

| Extra | Provider SDK installed |
|---|---|
| `openai` | `livekit-plugins-openai` |
| `deepgram` | `livekit-plugins-deepgram` |
| `anthropic` | `livekit-plugins-anthropic` |
| `groq` | `livekit-plugins-groq` |
| `cartesia` | `livekit-plugins-cartesia` |
| `elevenlabs` | `livekit-plugins-elevenlabs` |
| `assemblyai` | `livekit-plugins-assemblyai` |
| `whisper` | `livekit-plugins-silero` + `openai-whisper` |

<Note>
  Each of the provider extras above implies `livekit`. You do not need to install
  `voicegateway[livekit]` separately when you install a provider extra.
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

| Extra | What it adds |
|---|---|
| `dashboard` | Prebuilt React dashboard bundle (served by `voicegw dashboard`) |
| `local` | Local model support: Whisper, Kokoro, Piper, Ollama |
| `mcp` | MCP server for IDE integration |
| `tui` | Terminal UI (Textual-based status / costs / sessions views) |
| `all` | Everything above |

You can combine any extras:

<CodeGroup>
```bash uv
uv pip install "voicegateway[livekit,dashboard,openai,deepgram]"
```
```bash pip
pip install "voicegateway[livekit,dashboard,openai,deepgram]"
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

You installed without the `deepgram` extra. Add it:

```bash
pip install "voicegateway[deepgram]"
```

**`ConfigError: No voicegw.yaml found`**

VoiceGateway searches in this order:

1. `./voicegw.yaml` (current directory)
2. `~/.config/voicegateway/voicegw.yaml`
3. `/etc/voicegateway/voicegw.yaml`

Set `VOICEGW_CONFIG` to an explicit path, or run `voicegw init` to generate a
starter config.

## Next steps

- [Quick start](/guide/quick-start): five-minute path from install to first cost row.
- [First agent](/guide/first-agent): a complete worked agent with `attach()` and `guard()`.
- [Frameworks and extras](/guide/frameworks): framework-neutral core explained.
