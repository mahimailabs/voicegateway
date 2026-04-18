# Examples

Practical examples showing how to use VoiceGateway in real-world scenarios. Each example includes runnable code and a complete `voicegw.yaml` configuration.

## Getting Started

Before running any example, install VoiceGateway with the providers you need:

```bash
# Install with cloud providers
pip install voicegateway[openai,deepgram,cartesia]

# Install with local providers
pip install voicegateway[whisper,kokoro]

# Install everything
pip install voicegateway[all,dev]
```

Then create a config file:

```bash
voicegw init
```

## Examples

### [Basic Voice Agent](./basic-voice-agent)

Build a simple voice agent using LiveKit Agents with VoiceGateway routing STT, LLM, and TTS requests. The minimal setup to get a working voice pipeline.

### [Multi-Project Setup](./multi-project)

Configure three separate projects (production, staging, dev) with different model stacks, budgets, and tags. Shows how to isolate cost tracking across teams and environments.

### [Budget Enforcement](./budget-enforcement)

Demonstrate all three budget modes: `warn` (log and continue), `throttle` (fall back to local models), and `block` (reject requests). Includes handling `BudgetExceededError` and `BudgetThrottleSignal`.

### [Fallback Chains](./fallback-chains)

Configure primary/backup model chains so that if Deepgram's STT is down, traffic automatically falls back to OpenAI Whisper, then to local Whisper. Includes monitoring fallback events.

### [Local-Only Deployment](./local-only)

Run VoiceGateway with zero cloud dependencies using Ollama for LLM, Whisper for STT, and Kokoro for TTS. Useful for air-gapped environments or development without API keys.

### [Claude Code Integration](./claude-code-integration)

Use VoiceGateway's MCP server with Claude Code to manage providers, models, projects, and monitor costs through natural language. Includes 5+ end-to-end prompt examples.

### [Docker Deployment](./docker-deployment)

Production-ready `docker-compose.yml` with the API server, dashboard, persistent storage, health checks, and optional Ollama sidecar.
