---
title: Installation
description: Install VoiceGateway with uv or pip. Pick the framework extra for LiveKit or Pipecat, then bring your own provider plugins. Python 3.11+ required.
---
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
# One provider
uv pip install livekit-plugins-openai

# Several at once
uv pip install livekit-plugins-openai livekit-plugins-deepgram livekit-plugins-cartesia
```
```bash pip
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
uv pip install "pipecat-ai[openai,deepgram,cartesia]"
```
```bash pip
pip install "pipecat-ai[openai,deepgram,cartesia]"
```
</CodeGroup>

## Additional extras

VoiceGateway ships four runtime extras (`dev` is separate, for contributors:
see [Install from source](#install-from-source)).

| Extra | What it adds |
|---|---|
| `livekit` | LiveKit Agents seam for `attach()` / `guard()` |
| `pipecat` | Pipecat seam for `attach()` / `guard()` |
| `dashboard` | HTTP API server, the prebuilt React dashboard (`voicegw dashboard`), and the MCP server (`voicegw mcp`) |
| `collector` | Self-hosted fleet collector: `dashboard` plus the Postgres + DuckDB backend |

There is no standalone `mcp` extra: `voicegw mcp` ships inside `dashboard`
(and so inside `collector` too). There are no per-provider or local-model
extras either. VoiceGateway meters native
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

`docker compose up` has no `-e` or `-v` flags. Provider keys and volume
mounts belong in the compose file's `environment:`/`volumes:` blocks, or in
a `.env` file Compose reads automatically. This repo's `docker-compose.yml`
does both already: it mounts `./voicegw.yaml` and reads keys like
`${DEEPGRAM_API_KEY}` from the environment.

```bash
voicegw init
docker compose up -d
```

<Note>
  Run `voicegw init` first. On the published `0.22.3` image a missing config file
  is fatal: the container exits at boot. A later release relaxes that to a warning
  and boots on built-in defaults, but a default-config daemon has no providers,
  models, or projects declared, so you want the file regardless.

  The CLI is stricter than the container either way. `voicegw serve`, `costs`,
  `logs`, and `status` all resolve a config and raise `ConfigError` when there is
  none, so a mistyped `--config` stays a hard error rather than silently starting
  on defaults.
</Note>

Full production setup (published image, `.env`, health checks, persistent
storage): [Docker deployment](/examples/docker-deployment).

## Verify

```bash
voicegw --version
voicegw status
```

If `voicegw` is not on your PATH after a `uv pip install`, activate the
environment, or call the entry point directly: `./.venv/bin/voicegw --version`
(`.venv\Scripts\voicegw.exe --version` on Windows).

## Upgrading

<CodeGroup>
```bash uv
uv pip install --upgrade voicegateway
```
```bash pip
pip install --upgrade voicegateway
```
</CodeGroup>

After upgrading, check for config schema changes by diffing your config
against a fresh reference copy:

```bash
voicegw init --full --output /tmp/voicegw.reference.yaml
diff voicegw.yaml /tmp/voicegw.reference.yaml
```

`voicegw init` only takes `--output`/`-o` and `--full`; there is no `--diff`
flag.

## Troubleshooting

**`ModuleNotFoundError: No module named 'deepgram'`**

Your agent is missing the Deepgram plugin. VoiceGateway does not install provider
wheels, so install the one your agent uses:

```bash
pip install livekit-plugins-deepgram
```

**`ConfigError: No voicegw.yaml found`**

Run `voicegw init` to generate a starter config, or set `VOICEGW_CONFIG` to
an explicit path. See [config discovery
order](/configuration/environment-variables#config-discovery-order) for the
default search path.

## Next steps

- [Quickstart](/get-started): five-minute path from install to first cost row.
- [First agent](/guide/first-agent): a complete worked agent with `attach()` and `guard()`.
- [Frameworks and extras](/guide/frameworks): framework-neutral core explained.
