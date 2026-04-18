# Installation

## System requirements

- **Python 3.11** or later
- **pip 21+** (for PEP 660 editable installs)
- **SQLite 3.35+** (ships with Python; used for cost tracking and request logs)
- **Docker** (optional, for containerized deployments)

## Install via pip

The base package installs VoiceGateway core with no provider SDKs:

```bash
pip install voicegateway
```

### Install extras

VoiceGateway uses optional extras to keep the install lightweight. Only the provider SDKs you need are installed.

| Extra | What it installs | Command |
|---|---|---|
| `cloud` | All cloud provider SDKs (Deepgram, OpenAI, Anthropic, Groq, Cartesia, ElevenLabs, AssemblyAI) | `pip install voicegateway[cloud]` |
| `local` | Local model dependencies (Whisper, Kokoro, Piper, Ollama) | `pip install voicegateway[local]` |
| `dashboard` | Web dashboard (FastAPI, React frontend) | `pip install voicegateway[dashboard]` |
| `mcp` | MCP server for IDE integration | `pip install voicegateway[mcp]` |
| `all` | Everything above | `pip install voicegateway[all]` |

You can combine extras:

```bash
pip install voicegateway[cloud,dashboard]
```

Or install individual provider SDKs:

```bash
pip install voicegateway[openai,deepgram]
```

## Install from source

```bash
git clone https://github.com/mahimai/voicegateway.git
cd voicegateway
pip install -e ".[dev]"
```

The `dev` extra includes test dependencies (pytest, pytest-asyncio, pytest-cov) and linting tools.

### Verify the installation

```bash
voicegw --version
voicegw status
```

If `voicegw` is not on your PATH, you can also run:

```bash
python -m voicegateway.cli --version
```

## Docker

VoiceGateway ships with a `docker-compose.yml` for running the API server and dashboard together:

```bash
# API + Dashboard
docker compose up -d

# API + Dashboard + Ollama (for local LLM)
docker compose --profile local up -d
```

The default Docker setup exposes:
- Port **8080** -- HTTP API
- Port **9090** -- Web dashboard

Mount your config file and set environment variables:

```bash
docker compose up -d \
  -e DEEPGRAM_API_KEY=your-key \
  -e OPENAI_API_KEY=your-key \
  -v ./voicegw.yaml:/app/voicegw.yaml
```

## Upgrading

```bash
pip install --upgrade voicegateway
```

After upgrading, check for config schema changes:

```bash
voicegw init --diff
```

This shows any new config options available in the latest version.

## Troubleshooting

**`ModuleNotFoundError: No module named 'deepgram'`**

You installed the base package without the provider extra. Install the extra you need:

```bash
pip install voicegateway[deepgram]
```

**`ConfigError: No voicegw.yaml found`**

VoiceGateway searches for config in this order:
1. `./voicegw.yaml` (current directory)
2. `~/.config/voicegateway/voicegw.yaml`
3. `/etc/voicegateway/voicegw.yaml`

You can also set the `VOICEGW_CONFIG` environment variable to an explicit path. Run `voicegw init` to generate a starter config.

## Next steps

- [Quick Start](/guide/quick-start) -- 5-minute walkthrough
- [First Agent](/guide/first-agent) -- build a working agent
- [Environment Variables](/configuration/environment-variables) -- all supported env vars
