# Migrating from LiveKit Cloud Inference

**The migration is one line.**

```diff
- from livekit.agents import inference
+ from voicegateway import inference
```

That swap routes every `inference.STT(...)`, `inference.LLM(...)`, and `inference.TTS(...)` call through your self-hosted VoiceGateway with your own provider keys. The rest of your `AgentSession` code does not change.

This page walks through the swap end to end: configure your providers, drop in the new import, see your costs in the dashboard, and understand the small set of LK Cloud features that do not carry over.

## Worked example

The same agent runs on either side. Only the import line changes.

### Before — LiveKit Cloud Inference

```python
from livekit.agents import AgentSession
from livekit.agents import inference

async def entrypoint():
    session = AgentSession(
        stt=inference.STT("deepgram/nova-3:en"),
        llm=inference.LLM("openai/gpt-4o-mini"),
        tts=inference.TTS("cartesia/sonic-3:my-voice-id"),
    )
    await session.start()
```

### After — VoiceGateway

```python
from livekit.agents import AgentSession
from voicegateway import inference         # only line that changed

async def entrypoint():
    session = AgentSession(
        stt=inference.STT("deepgram/nova-3:en"),
        llm=inference.LLM("openai/gpt-4o-mini"),
        tts=inference.TTS("cartesia/sonic-3:my-voice-id"),
    )
    await session.start()
```

`voicegateway.inference.STT/LLM/TTS` mirror `livekit.agents.inference.STT/LLM/TTS` parameter for parameter — name, kind, default. The drop-in compatibility test in the VG repo (`tests/inference/test_drop_in_compatibility.py`) runs on every CI build to keep that promise honest as LiveKit ships new releases.

## Configure your providers

VoiceGateway uses your own provider keys. After installing `voicegateway[cloud,dashboard]`, create a `voicegw.yaml` next to your agent code (the gateway searches `./voicegw.yaml`, then `~/.config/voicegateway/voicegw.yaml`, then `/etc/voicegateway/voicegw.yaml`; override with the `--config` flag on every CLI command):

```yaml
projects:
  voice-app:
    name: My Voice App
    daily_budget: 50.00
    budget_action: warn
    providers:
      openai:
        api_key: ${OPENAI_API_KEY}
      deepgram:
        api_key: ${DEEPGRAM_API_KEY}
      cartesia:
        api_key: ${CARTESIA_API_KEY}

default_project: voice-app

cost_tracking:
  enabled: true
```

`${OPENAI_API_KEY}` is **VoiceGateway's** YAML interpolation — the gateway reads each `${VAR_NAME}` from your shell environment when it loads the config (env-var indirection, not Python f-strings, not Bash). Export the keys in your shell (or your deployment's secret manager) before starting the agent or `voicegw serve`.

The eleven supported providers are: `openai`, `deepgram`, `anthropic`, `groq`, `cartesia`, `elevenlabs`, `assemblyai`, `ollama`, `whisper`, `kokoro`, `piper`. Match the provider name on the left side of `provider/model` slashes exactly — e.g. `inference.STT("deepgram/nova-3")`, never `inference.STT("Deepgram/nova-3")`.

`default_project: voice-app` tells the inference module which project to charge against when your code does not call `inference.set_project(...)` explicitly. If you have multiple agents running under one VG install, give each its own project entry — that is how every cost row, latency sample, and session record gets tagged.

## Session correlation

VoiceGateway tags every STT, LLM, and TTS request from the same `AgentSession` with one shared `session_id`. Your dashboard's **Sessions** view groups them by call so cost and latency questions ("what did the last call cost?") have answers without you wiring anything.

The session id is derived from a Python `ContextVar`. Inside the standard `AgentSession` lifecycle it just works. The known limit: factories constructed in **separate** asyncio tasks created before the session opens may end up in different contexts and produce different ids. Concrete shape:

```python
# Safe — factories constructed inside the entrypoint, after the
# AgentSession is being built. All three share one session_id.
async def entrypoint():
    session = AgentSession(
        stt=inference.STT("deepgram/nova-3:en"),
        llm=inference.LLM("openai/gpt-4o-mini"),
        tts=inference.TTS("cartesia/sonic-3:my-voice-id"),
    )
    await session.start()

# Unsafe — module-level factory captured an empty context. The
# request rows from `_warm_stt` will land with a different
# session_id (or none) than the runtime AgentSession.
_warm_stt = inference.STT("deepgram/nova-3:en")  # don't do this
```

See [Limitations](#limitations) below for the full edge-case write-up.

## Cost comparison

| Component | LiveKit Cloud Inference (estimated) | VoiceGateway with direct keys |
|---|---|---|
| STT (Deepgram Nova-3, 10,000 min) | bundled in LK plan | $43.00 |
| LLM (GPT-4o-mini, 5M tokens) | bundled in LK plan | $3.75 |
| TTS (Cartesia Sonic-3, 2M chars) | bundled in LK plan | $130.00 |
| VoiceGateway runtime | n/a | $0 (MIT license) |
| **Total / month** | varies by LK plan | **$176.75** |

> Pricing snapshot from each provider's pricing page as of 2026-05-04. Confirm against the live page (Deepgram, OpenAI, Cartesia) before basing a migration decision on these numbers; provider rates change.

If your LK Cloud bill last month was, say, $400 for this kind of workload, the VG-direct cost is the line item above ($177) plus the savings of skipping LK's inference markup. Run [`voicegw reconcile`](/cli/reconcile) on a real export to compare your bill.

## Manage keys from your coding agent (optional)

This section is **optional** — single-agent setups manage keys via `voicegw.yaml` and never need the MCP surface. Skip ahead to [Dashboard](#dashboard) if that's you.

For multi-environment or agent-driven workflows, VoiceGateway ships an MCP server exposing five v0.0.5 tools for per-project provider key management:

- `vg_add_provider(project, provider, api_key)` — Fernet-encrypted at rest.
- `vg_set_provider_key(project, provider, api_key)` — rotation path; errors when the key does not yet exist.
- `vg_remove_provider(project, provider)` — drop a key.
- `vg_list_providers(project=...)` — surface all keys with masks.
- `vg_test_provider_key(project, provider)` — runs the underlying provider's lightweight health check.

Wire them up in your Claude Code or other MCP-aware client and ask it to "add my Deepgram key for the voice-app project". See the [MCP setup guide](/mcp/setup) for the connection details (registration manifest, transports, auth).

## Dashboard

```bash
voicegw dashboard
```

Opens at `http://localhost:9090`. The new **Providers** page (v0.0.5) lists per-project provider keys grouped by project, with Test / Rotate / Delete actions and a green/red status dot showing the last test result. Cost dashboards, latency percentiles, and request logs continue to work exactly as before.

## Limitations

Two real, two minor.

### 1. Session correlation needs the standard async flow

VoiceGateway derives the per-session id from a Python `ContextVar` set by the first `inference.STT/LLM/TTS` factory call in the active context. AgentSession constructs all three from one place, so the id propagates naturally.

The gap: if your code constructs an STT or LLM in a separate `asyncio.Task` created **before** the session begins, that task captured an empty context and gets its own id. The dashboard surfaces this as orphaned-request rows. Workaround: construct the factories at session entry, not at module import time. Future v0.0.6+ work will add an explicit `session_id` escape hatch for the orchestration cases.

### 2. The `api_secret` parameter is ignored

LiveKit Cloud uses `api_secret` to sign access tokens against its inference gateway. VoiceGateway uses your raw provider keys directly — there is no token to sign — so passing `api_secret` produces a one-time `UserWarning` and the value is discarded. Drop the parameter from your factory calls; the rest of the signature is identical:

```diff
- inference.STT("deepgram/nova-3", api_key=DG_KEY, api_secret=DG_SECRET)
+ inference.STT("deepgram/nova-3", api_key=DG_KEY)
```

If you don't pass `api_secret` today (most agents don't), there's nothing to change.

### 3. `fallback` and `conn_options` are accepted but not yet honored

The LK 1.5.7 signatures expose `fallback` and `conn_options`. VoiceGateway accepts both for drop-in compatibility (so existing user code compiles) but emits a `UserWarning` and falls back to behavior driven by your `voicegw.yaml`'s `fallbacks:` block. Resolver-time fallback chains in YAML cover the most common case; runtime mid-call failover lands in v0.0.6+.

### 4. LiveKit Cloud's hosted billing dashboard is not replaced

VoiceGateway replaces inference *routing*. It does not replace LiveKit Cloud's billing UI, agent deployment automation, or LiveKit-managed credentials. You still use LK rooms, tracks, and WebRTC infrastructure for media transport — VG plugs into `livekit-agents` as a provider source, not a transport layer.

## Troubleshooting

The three things most likely to break a first migration:

### Costs landing on the `default` project instead of yours

Voicegw.yaml has `projects:` configured, but no `default_project` is set and your code never calls `inference.set_project(...)`. The gateway auto-creates a project named `"default"` on first run, so the resolver lands there instead of erroring. Per-project provider keys for your real project never get used. Fix one of:

```yaml
default_project: voice-app
```

Or in code before constructing factories:

```python
from voicegateway import inference
inference.set_project("voice-app")
```

Or via env var (useful for per-deployment overrides):

```bash
export VOICEGW_ACTIVE_PROJECT=voice-app
```

Tell-tale sign: `voicegw projects` shows costs accumulating on `default` while your `voice-app` row stays at `$0.00`.

### "ModelResolutionError: Unknown provider 'foo'"

Your model string used a provider name VoiceGateway does not recognize. The eleven supported providers are: `openai`, `deepgram`, `anthropic`, `groq`, `cartesia`, `elevenlabs`, `assemblyai`, `ollama`, `whisper`, `kokoro`, `piper`. Match the provider name on the left side of the slash exactly.

### Ollama tag stripped

If you call `inference.LLM("ollama/qwen2.5:3b")` and end up with model `qwen2.5` instead of `qwen2.5:3b`, that is by design — `inference.STT` and `inference.TTS` strip a trailing `:suffix` because LK Cloud uses it for language and voice respectively. `inference.LLM` does **not** strip the suffix because LK Cloud's LLM never adopted that convention. So `inference.LLM("ollama/qwen2.5:3b")` works as you would expect; `inference.STT("ollama/...:3b")` would lose the `3b` part. STT and TTS factories that need a colon-bearing model name should pass it without the colon and use the relevant kwarg (`language=` or `voice=`).

## Keeping LiveKit for real-time transport

VoiceGateway replaces LiveKit's inference routing. It does **not** replace LiveKit's real-time transport — rooms, tracks, WebRTC, agent deployment. Continue to use those exactly as before; VG plugs into `livekit-agents` as a provider source. The `AgentSession` API, the WebRTC plumbing, and the room lifecycle stay unchanged.

## Related pages

- [Quick Start](/guide/quick-start)
- [Per-project providers](/configuration/projects) — the v0.0.5 yaml shape in detail
- [Dashboard API reference](/api/dashboard-api) — endpoints the UI consumes
- [MCP setup](/mcp/setup) — vg_add_provider and friends
- [Migrating from LiteLLM](/migration/from-litellm)
- [Version Upgrades](/migration/version-upgrades)
- [Troubleshooting](/reference/troubleshooting)
