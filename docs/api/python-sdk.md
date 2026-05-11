# Python SDK Reference

VoiceGateway exposes a single public Python surface as of v0.0.5: the `voicegateway.inference` module, a drop-in mirror of `livekit.agents.inference`. New agent code uses it; existing LiveKit Cloud Inference code migrates with one import-line change.

Cost queries, project management, latency stats, and request logs live outside the Python SDK. Use the [CLI](/cli/), the [HTTP API](/api/http-api), the [dashboard](/), or the [MCP tools](/mcp/) for those.

## Installation

```bash
pip install voicegateway
# Or with specific provider extras:
pip install "voicegateway[openai,deepgram,cartesia]"
```

## Import

```python
from voicegateway import inference
```

The `inference` submodule is the only documented public entry point. The internal `voicegateway.core.gateway.Gateway` class still exists for the CLI, HTTP server, and MCP runtime, but it is not part of the supported Python SDK and may change without notice.

## `inference.STT`

```python
inference.STT(
    model: NotGivenOr[STTModels | str] = NOT_GIVEN,
    *,
    language: NotGivenOr[str] = NOT_GIVEN,
    base_url: NotGivenOr[str] = NOT_GIVEN,
    encoding: NotGivenOr[STTEncoding] = NOT_GIVEN,
    sample_rate: NotGivenOr[int] = NOT_GIVEN,
    api_key: NotGivenOr[str] = NOT_GIVEN,
    api_secret: NotGivenOr[str] = NOT_GIVEN,
    http_session: aiohttp.ClientSession | None = None,
    extra_kwargs: NotGivenOr[dict | DeepgramOptions | ...] = NOT_GIVEN,
    fallback: NotGivenOr[list[FallbackModelType] | FallbackModelType] = NOT_GIVEN,
    conn_options: NotGivenOr[APIConnectOptions] = NOT_GIVEN,
)
```

```python
from voicegateway import inference

stt = inference.STT("deepgram/nova-3:en")
# Trailing :en parses as the language (mirrors LK STT).
```

The `model` string parses as `provider/model[:language]`. Provider names are validated against the eleven supported types (`openai`, `deepgram`, `cartesia`, `anthropic`, `groq`, `elevenlabs`, `assemblyai`, `ollama`, `whisper`, `kokoro`, `piper`). The `api_key` kwarg, when given, overrides the project's resolved key for this one instance — useful for testing.

`api_secret`, `fallback`, and `conn_options` are accepted for drop-in compatibility but emit a `UserWarning`; v0.0.6+ will honor `fallback`.

## `inference.LLM`

```python
inference.LLM(
    model: LLMModels | str,
    *,
    provider: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
    inference_class: InferenceClass | None = None,
    extra_kwargs: ChatCompletionOptions | dict | None = None,
)
```

```python
llm = inference.LLM("openai/gpt-4o-mini")

# Ollama tags are preserved: LLM does NOT strip the trailing colon
# segment (only STT and TTS do).
llm = inference.LLM("ollama/qwen2.5:3b")

# Explicit provider= overrides any leading "<provider>/" segment in
# the model string. Useful when the model name itself has no slash.
llm = inference.LLM("gpt-4o-mini", provider="openai")
```

LLM uses `None` defaults instead of `NotGivenOr` to match LK's LLM shape. There is no `fallback`, `conn_options`, or `http_session` parameter; those are STT/TTS-specific.

## `inference.TTS`

```python
inference.TTS(
    model: TTSModels | str,
    *,
    voice: NotGivenOr[str] = NOT_GIVEN,
    language: NotGivenOr[str] = NOT_GIVEN,
    encoding: NotGivenOr[TTSEncoding] = NOT_GIVEN,
    sample_rate: NotGivenOr[int] = NOT_GIVEN,
    base_url: NotGivenOr[str] = NOT_GIVEN,
    api_key: NotGivenOr[str] = NOT_GIVEN,
    api_secret: NotGivenOr[str] = NOT_GIVEN,
    http_session: aiohttp.ClientSession | None = None,
    extra_kwargs: NotGivenOr[dict | CartesiaOptions | ...] = NOT_GIVEN,
    fallback: NotGivenOr[list[FallbackModelType] | FallbackModelType] = NOT_GIVEN,
    conn_options: NotGivenOr[APIConnectOptions] = NOT_GIVEN,
)
```

```python
tts = inference.TTS("cartesia/sonic-3:my-voice-id")
# Trailing :my-voice-id parses as the voice (mirrors LK TTS).

# Or explicit voice kwarg:
tts = inference.TTS("cartesia/sonic-3", voice="my-voice-id")
```

Same shape as STT, plus a `voice` kwarg. The trailing colon-suffix in the model string parses as voice (NOT language) — that is the semantic asymmetry between STT and TTS that LiveKit defines.

## Project routing

### `inference.set_project`

```python
inference.set_project(name: str) -> None
```

```python
from voicegateway import inference

inference.set_project("tony-pizza")
stt = inference.STT("deepgram/nova-3")  # uses tony-pizza's key
```

Sets the active project for the current async context. The setting inherits across awaited coroutines but is isolated across separate `asyncio.Task` instances.

Resolution order for the active project:

1. `inference.set_project(name)` in the current context.
2. `VOICEGW_ACTIVE_PROJECT` environment variable.
3. `default_project` field in `voicegw.yaml`.
4. The literal `"default"`. The gateway auto-creates a project of this id on first run, so the fallback is always backed by a real row.

### `inference.get_active_project`

```python
inference.get_active_project() -> str
```

Returns the active project name following the resolution order above.

```python
from voicegateway import inference

print(f"Resolving keys for project: {inference.get_active_project()}")
```

## Session correlation

### `inference.start_session`

```python
inference.start_session() -> str
```

VoiceGateway tags every STT, LLM, and TTS call from the same async context with one shared `session_id` (`"vg-<uuid4>"`). Inside `AgentSession` this happens automatically: the first factory constructed in a context creates the id, the others inherit it. The id is written to `requests.session_id` and accumulates into the `sessions` table.

The standard `livekit-agents` worker spawns a fresh task per call, so the ContextVar starts clean and `start_session` is unnecessary. Worker patterns that handle multiple conversations sequentially in a single asyncio task need to call `start_session()` at the top of each conversation handler; otherwise the second conversation reuses the first's id.

```python
from voicegateway import inference

async def handle_conversation():
    session_id = inference.start_session()  # rolls a fresh id
    stt = inference.STT("deepgram/nova-3")
    llm = inference.LLM("openai/gpt-4o-mini")
    tts = inference.TTS("cartesia/sonic-3")
    # ... session_id is shared across all three modalities ...
```

The known gap: factories constructed in separate `asyncio.Task` instances created **before** the session opens get their own ids. Construct factories at session entry, not at module import time. See the [from-livekit-inference migration guide](/migration/from-livekit-inference#limitations) for details.

### `inference.attach_session` (v0.2.0, opt-in)

```python
inference.attach_session(
    agent_session,
    *,
    session_id: str | None = None,
    turn_tracker: TurnTracker | None = None,
    dead_air_detector: DeadAirDetector | None = None,
    cost_tracker: CostTracker | None = None,
) -> str
```

Opt-in escape hatch that wires a LiveKit `AgentSession` into the v0.2.0 voice-conversation metrics pipeline: per-turn response speed (REQ-VG-METRICS-002), talk-over rate (REQ-VG-METRICS-003), and dead-air detection (REQ-VG-METRICS-004).

In the standard `livekit-agents` worker pattern, the metric capture happens automatically through plugin-level hooks on `InstrumentedSTT`/`InstrumentedTTS`. `attach_session` exists for the cases where those hooks miss events: custom AgentSession subclasses, in-process agent harnesses, or test rigs. When in doubt, you don't need to call it.

Returns the bound `session_id` so the caller can echo it into its own logs.

```python
from livekit.agents import AgentSession
from voicegateway import inference

async def handle_call():
    agent_session = AgentSession(...)  # your usual construction

    # Opt into explicit metric wiring.
    sid = inference.attach_session(agent_session)

    await agent_session.start(...)
    # Per-turn captures flow into the TurnTracker; the AgentSession's
    # `close` event flushes them, stops the dead-air watcher, and
    # calls cost-tracker's session-finalization hook.
```

The helper subscribes to five `AgentSession` events: `user_started_speaking`, `user_stopped_speaking`, `agent_started_speaking`, `agent_stopped_speaking`, `close`. The first four feed the `TurnTracker`; `close` flushes the tracker, stops the `DeadAirDetector`, and calls `CostTracker.close_session(sid)` so the v0.2.0 aggregate columns (`talk_time_seconds`, `per_minute_cost_usd`, `response_speed_p50/p95_ms`, `talk_over_rate`) land on the `sessions` row by the time the dashboard's `/api/metrics` endpoint reads it.

Components default to the process-level registry the Gateway populates on startup; pass explicit kwargs to override (the unit-test path).

## Operations: where to go

| You want to | Use this |
|---|---|
| List projects | `voicegw projects` (CLI), `GET /v1/projects` (HTTP), `list_projects` (MCP) |
| See costs | `voicegw costs` (CLI), `GET /v1/costs` (HTTP), `get_costs` (MCP), the dashboard |
| Tail recent requests | `voicegw logs` (CLI), `GET /v1/logs` (HTTP), `get_logs` (MCP) |
| Add or rotate a provider key | `vg_add_provider` / `vg_set_provider_key` (MCP), the dashboard Providers page |
| Reconcile against an invoice | `voicegw reconcile --provider <name> --provider-usage-file <path>` |

The Python SDK does not include these helpers; they live in the surfaces above.
