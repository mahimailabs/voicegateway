---
title: Installation
description: Every install path for VoiceGateway. curl-bash recommended for first-run; pipx / uv / Docker for everything else.
---

# Installation

## System requirements

- **Python 3.11** or later
- **SQLite 3.35+** (ships with Python; used for cost tracking and
  request logs)
- **macOS, Linux, or WSL on Windows.** Native Windows is supported
  via Scheduled Tasks for the daemon; the rest of the docs assume a
  POSIX shell.
- **Docker** (optional, for containerised deployments)

## Recommended: curl-bash one-liner

```bash
curl -fsSL https://voicegateway.mahimai.ca/install.sh | bash
```

The installer:

- Detects your OS (macOS / Linux / WSL).
- Verifies Python 3.11+ is installed (refuses with package-manager
  pointers if not; does not auto-install Python).
- Picks `uv tool install` if uv is on PATH, otherwise installs pipx
  and runs `pipx install voicegateway[cloud,dashboard]`.
- Asks before any privileged step.

After install, run `voicegw onboard`. See [Get started](/get-started)
for the 60-second walkthrough.

## pipx (manual)

```bash
pipx install 'voicegateway[cloud,dashboard]'
```

`pipx` installs VoiceGateway into its own virtualenv so the `voicegw`
binary lands on your PATH without polluting your system Python.

## uv (manual)

```bash
uv tool install 'voicegateway[cloud,dashboard]'
```

`uv tool install` is faster than pipx and uses the same per-tool-venv
model. If you have uv already, prefer this.

## Install extras

VoiceGateway uses optional extras to keep the install lightweight.
Only the provider SDKs you need are installed.

| Extra | What it installs |
|---|---|
| `cloud` | All cloud provider SDKs (Deepgram, OpenAI, Anthropic, Groq, Cartesia, ElevenLabs, AssemblyAI) |
| `local` | Local model dependencies (Whisper, Kokoro, Piper, Ollama) |
| `dashboard` | Web dashboard (React bundle + Pillow for logo validation) |
| `mcp` | MCP server for IDE integration |
| `tui` | Terminal UI (Textual-based status / costs / sessions views) |
| `all` | Everything above |

You can combine extras:

```bash
pipx install 'voicegateway[cloud,dashboard,mcp]'
```

Or install individual provider SDKs:

```bash
pipx install 'voicegateway[openai,deepgram]'
```

## From source

```bash
git clone https://github.com/mahimailabs/voicegateway.git
cd voicegateway
pip install -e ".[dev]"
```

The `dev` extra includes test dependencies (pytest, pytest-asyncio,
pytest-cov, ruff, mypy).

Running from source ships the React frontend as source. Build it:

```bash
cd src/dashboard/frontend
npm install
npm run build
```

The daemon mounts `src/dashboard/frontend/dist/` at `/` once that
exists.

## Docker

VoiceGateway ships a `docker-compose.yml` for running the daemon
(serving both the HTTP API and the dashboard):

```bash
# Daemon (HTTP API + dashboard on one port)
docker compose up -d

# Plus Ollama (for local LLM)
docker compose --profile local up -d
```

The default Docker setup exposes port **8080**: the daemon serves
`/v1/*` (HTTP API), `/api/*` (dashboard API), and `/` (React UI)
on that port. Mount your config and set environment variables:

```bash
docker compose up -d \
  -e DEEPGRAM_API_KEY=your-key \
  -e OPENAI_API_KEY=your-key \
  -v ./voicegw.yaml:/app/voicegw.yaml
```

## Verify the install

```bash
voicegw --version
voicegw status
```

If `voicegw` is not on your PATH, run:

```bash
python -m voicegateway.cli --version
```

For pipx, you may need `pipx ensurepath && exec $SHELL`.

## Upgrading

```bash
pipx upgrade voicegateway
```

Or with uv:

```bash
uv tool upgrade voicegateway
```

After upgrading, check for config schema changes:

```bash
voicegw init --diff
```

## Troubleshooting

**`ModuleNotFoundError: No module named 'deepgram'`**

You installed the base package without the provider extra. Install
the extra you need:

```bash
pipx install 'voicegateway[deepgram]'
```

**`ConfigError: No voicegw.yaml found`**

VoiceGateway searches for config in this order:

1. `./voicegw.yaml` (current directory)
2. `~/.config/voicegateway/voicegw.yaml`
3. `/etc/voicegateway/voicegw.yaml`

You can also set the `VOICEGW_CONFIG` environment variable to an
explicit path. Run `voicegw init` to generate a starter config or
`voicegw onboard` to be walked through it.

## Next steps

- [Get started](/get-started): 60-second walkthrough.
- [Quick start](/guide/quick-start): the 5-minute version that
  exercises the inference factories.
- [First agent](/guide/first-agent): build a working agent.
- [Environment variables](/configuration/environment-variables):
  all supported env vars.
