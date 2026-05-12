# Version Upgrades

This page documents breaking changes, migration steps, and upgrade notes for each VoiceGateway release.

## Upgrade process

For any version upgrade:

```bash
# 1. Check your current version
voicegw --version

# 2. Upgrade
pip install --upgrade voicegateway

# 3. Check for config schema changes
voicegw init --diff

# 4. Run your tests
pytest

# 5. Restart the server
voicegw serve --port 8080
```

If you use Docker:

```bash
docker compose pull
docker compose up -d
```

## Version history

### v0.6.0 -- Voice-specific guardrails

**Release date:** TBD

This release adds project-scoped, LLM-side guardrails for LiveKit voice agents. Existing configs continue to load; guardrails default to disabled.

**Features:**

- **Project guardrail policies** -- categories `pii`, `financial`, `medical`, `prompt_injection`, and `off_topic`; actions `redact`, `block`, `alert`, and `off`
- **Prompt/tool injection** -- `voicegateway.inference.LLM.chat(...)` appends a versioned guardrail prompt block and registers the reserved LiveKit tool `report_guardrail_action`
- **Policy persistence** -- SQLite overlay column `managed_projects.guardrail_policy_json`, including projects originally defined in YAML
- **Session audit state** -- sessions store `guardrails_active`, `guardrails_bypassed`, and `guardrail_policy_snapshot_json`
- **Audit events** -- new `guardrail_events` table records `fired` and `bypassed` rows
- **APIs and dashboard** -- `/v1/guardrails/events`, `/v1/guardrails/aggregate`, project policy endpoints, a Guardrails dashboard page, and policy editing on Projects
- **CLI** -- `voicegw guardrails show|set|clear|dry-run --project ...`

**Migration notes:**

- No config change is required unless you want guardrails active.
- The migration adds nullable columns and preserves existing session/project rows.
- Active sessions keep the policy snapshot frozen on their first guarded LLM chat. Policy edits apply to later sessions.
- User-defined LiveKit tools named `report_guardrail_action` are rejected when guardrails are active because the name is reserved by VoiceGateway.
- `inference.start_session(bypass_guardrails=True)` and `inference.attach_session(..., bypass_guardrails=True)` skip injection and write a bypass audit row when the policy would otherwise be active.

**Config example:**

```yaml
projects:
  support:
    name: Support Bot
    guardrails:
      enabled: true
      categories:
        pii: redact
        financial: block
        medical: alert
        prompt_injection: block
        off_topic: off
```

See the [guardrails guide](/guide/guardrails) and [guardrail prompt reference](/reference/guardrail-prompts).

---

### v0.1.0 -- Initial release

**Release date:** 2026-04-17

This is the first public release of VoiceGateway. There are no breaking changes to migrate from.

**Features:**

- **Gateway core** -- unified routing for STT, LLM, and TTS requests through `Gateway.stt()`, `Gateway.llm()`, `Gateway.tts()`
- **11 providers** -- OpenAI, Deepgram, Anthropic, Groq, Cartesia, ElevenLabs, AssemblyAI (cloud); Whisper, Kokoro, Piper, Ollama (local)
- **Configuration** -- YAML config at `voicegw.yaml` with `${ENV_VAR}` substitution
- **Cost tracking** -- per-request cost calculation using built-in pricing catalog, stored in SQLite
- **Budget enforcement** -- per-project daily budgets with `warn` or `block` actions
- **Fallback chains** -- per-modality fallback when primary provider fails
- **Rate limiting** -- configurable per-provider rate limits
- **Latency monitoring** -- TTFB and total latency tracking per request
- **Request logging** -- full request metadata stored for audit and debugging
- **Web dashboard** -- React/TypeScript frontend with cost charts, latency graphs, request logs
- **HTTP API** -- FastAPI server with `/health`, `/v1/status`, `/v1/models`, `/v1/costs`, `/v1/projects`, `/v1/logs`, `/v1/metrics`
- **MCP server** -- 17 tools for managing the gateway from Claude Code, Cursor, Codex, and other coding agents
- **CLI** -- `voicegw init`, `voicegw serve`, `voicegw dashboard`, `voicegw status`, `voicegw mcp`
- **Docker support** -- `docker-compose.yml` with optional Ollama profile
- **Modular installs** -- `pip install voicegateway[openai,deepgram]` installs only the SDKs you need

**Config format (v0.1.0):**

```yaml
providers:
  <name>:
    api_key: ${ENV_VAR}
    # provider-specific options

models:
  stt:
    <provider/model>:
      provider: <name>
      model: <model>
  llm: { ... }
  tts: { ... }

stacks:
  <name>:
    stt: <provider/model>
    llm: <provider/model>
    tts: <provider/model>

projects:
  <slug>:
    name: <display name>
    daily_budget: <float>
    budget_action: warn | block

fallbacks:
  stt: [...]
  llm: [...]
  tts: [...]

cost_tracking:
  enabled: true
  db_path: <path>  # optional, defaults to ~/.config/voicegateway/voicegw.db
```

---

*Future releases will be documented here with breaking changes, deprecations, and migration steps.*

## Versioning policy

VoiceGateway follows [Semantic Versioning](https://semver.org/):

- **Patch** (0.1.x): bug fixes, no config changes
- **Minor** (0.x.0): new features, backward-compatible config changes
- **Major** (x.0.0): breaking changes to config format, Python API, or HTTP API

Before any breaking change, VoiceGateway will:

1. Deprecate the old behavior with a warning for at least one minor release
2. Document the migration path on this page
3. Provide `voicegw init --diff` output showing required config changes

## Related pages

- [Changelog](/reference/changelog)
- [Installation](/guide/installation)
- [Migrating from LiteLLM](/migration/from-litellm)
- [Migrating from LiveKit Inference](/migration/from-livekit-inference)
