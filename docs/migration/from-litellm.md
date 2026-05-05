# Migrating from LiteLLM

[LiteLLM](https://docs.litellm.ai/) is the dominant LLM gateway in the Python ecosystem: 100+ LLM providers, an OpenAI-compatible HTTP proxy, multi-level budgets, and a mature admin UI. Since early 2026 LiteLLM also ships `/v1/audio/transcriptions` (Whisper, Deepgram, ElevenLabs Scribe) and `/v1/audio/speech` (OpenAI, Azure, Gemini, ElevenLabs). For most teams it is the right LLM gateway.

VoiceGateway is not a replacement for LiteLLM as a general gateway. It is a complementary tool for one specific shape of workload: a LiveKit voice agent that needs cost visibility per modality (audio-minutes, tokens, characters) and reconciliation against provider invoices.

## Where each one fits

| Use case | Better fit |
|---|---|
| Text-only LLM application (chatbot, RAG, code-gen) | LiteLLM |
| Multi-provider LLM routing with an OpenAI-compatible HTTP proxy | LiteLLM |
| 100+ LLM provider catalog | LiteLLM |
| Multi-tenant, horizontally scaled gateway with PostgreSQL backend | LiteLLM |
| Per-key / per-team / per-user / per-model / per-agent budget granularity | LiteLLM |
| LiveKit voice agent with per-project cost tracking | VoiceGateway |
| Modality-aware unit accounting (per-minute STT, per-character TTS) backed by `pydantic/genai-prices` | VoiceGateway |
| Reconciliation tooling (`voicegw reconcile`) against provider invoices | VoiceGateway |
| Agent-managed configuration via MCP (Claude Code, Cursor, Codex, Cline) | VoiceGateway |
| Local model unification (Whisper, Kokoro, Piper, Ollama) without a network hop | VoiceGateway |

## Using both together

The two tools are not mutually exclusive. A common composition:

- **LiteLLM** as the LLM proxy for non-LiveKit workloads (background text processing, batch jobs, chat APIs, RAG pipelines).
- **VoiceGateway** for the LiveKit voice agent path, returning native LiveKit plugin instances and tracking per-modality cost against the agent's project budget.

Both tools can read from the same provider API keys; they do not contend for any state.

## When to migrate

Migrate from LiteLLM to VoiceGateway only if both of these are true:

1. You are building a LiveKit voice agent (or about to be).
2. You want cost tracking that is unit-aware per modality (STT in audio-minutes, LLM in tokens, TTS in characters) with reconciliation against your actual provider invoices.

If only #1 is true and you are happy with current cost tracking, keep LiteLLM and add VoiceGateway alongside.

## Migration steps (LiveKit voice agent only)

### 1. Install VoiceGateway

```bash
pip install "voicegateway[cloud,dashboard,mcp]"
```

### 2. Create a voicegw.yaml

```bash
voicegw init
```

Add the same provider API keys you already use with LiteLLM:

```yaml
providers:
  openai:
    api_key: ${OPENAI_API_KEY}
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}
  cartesia:
    api_key: ${CARTESIA_API_KEY}

models:
  llm:
    openai/gpt-4o-mini:
      provider: openai
      model: gpt-4o-mini
    anthropic/claude-3.5-sonnet:
      provider: anthropic
      model: claude-3.5-sonnet
  stt:
    deepgram/nova-3:
      provider: deepgram
      model: nova-3
  tts:
    cartesia/sonic-3:
      provider: cartesia
      model: sonic-3
```

### 3. Replace LiteLLM calls in your LiveKit agent

Where your LiveKit agent currently uses raw provider plugins or LiteLLM-backed factories, switch to `gw.stt()`, `gw.llm()`, `gw.tts()`. They return native LiveKit plugin instances that drop straight into `AgentSession`:

```python
from voicegateway import Gateway
from livekit.agents import AgentSession

gw = Gateway()

session = AgentSession(
    stt=gw.stt("deepgram/nova-3", project="my-app"),
    llm=gw.llm("openai/gpt-4o-mini", project="my-app"),
    tts=gw.tts("cartesia/sonic-3", project="my-app"),
)
```

For non-agent text workloads, keep using LiteLLM. The Gateway is not intended to replace it for that path.

### 4. Add a project budget

```yaml
projects:
  my-app:
    name: My Voice Application
    daily_budget: 25.00
    budget_action: warn  # warn | throttle | block
```

### 5. Verify costs against your provider invoice

After the agent has been running for a billing period:

```bash
voicegw export-costs --start 2026-04-01 --end 2026-04-30 --format csv
voicegw reconcile --provider openai --provider-usage-file openai-april-usage.csv
```

LLM cost is estimated from [`pydantic/genai-prices`](https://github.com/pydantic/genai-prices) and may drift up to ~5%. Reconciling against your provider invoice is the verification path; the diff table flags any per-model gaps.

### 6. (Optional) Enable the MCP server

```bash
voicegw mcp --transport stdio
```

Add to your Claude Code or Cursor config to manage the gateway from your editor. See the [MCP documentation](/mcp/) for setup details.

## A note on the audio endpoints

LiteLLM's `/v1/audio/transcriptions` and `/v1/audio/speech` cover the same provider surface (OpenAI Whisper, Deepgram, ElevenLabs, Cartesia for some modalities) and are well suited to non-agent audio pipelines (batch transcription, async TTS rendering, server-to-server audio API calls). Where they differ from VoiceGateway:

- LiteLLM exposes audio as HTTP endpoints in OpenAI shape; clients call them like any other OpenAI API.
- VoiceGateway returns LiveKit plugin instances that participate in a `livekit-agents` `AgentSession` directly, with cost tracking, per-project budgets, and modality-aware pricing wired through the wrapper.

If your audio workload is request/response (transcribe a file, render a string), LiteLLM is the better fit. If your audio workload is a real-time LiveKit voice agent, VoiceGateway is purpose-built for it.

## Related

- [Quick Start](/guide/quick-start)
- [Decision tree](/guide/decision-tree) (short page on which tool is right for your workload)
- [Cost reconciliation walkthrough](/guide/cost-reconciliation)
- [LiveKit FallbackAdapter integration](/examples/livekit-fallback-adapter)
- [LiteLLM docs](https://docs.litellm.ai/)
