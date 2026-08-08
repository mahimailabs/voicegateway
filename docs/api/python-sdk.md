---
title: Python SDK Reference
description: Public Python API for attaching VoiceGateway cost tracking and fallback to LiveKit and Pipecat agents.
---

The public Python surface is five names exported from the top-level `voicegateway` package: `attach`, `guard`, `register_worker`, `Observer`, and `__version__`.

```python
from voicegateway import attach, guard, register_worker, Observer, __version__
```

Cost queries, project management, latency stats, and request logs live outside the Python SDK. Use the [CLI](/cli/index), the [HTTP API](/api/http-api), the [Dashboard API](/api/dashboard-api), or the [MCP tools](/mcp/index) for those.

## Installation

<CodeGroup>

```bash uv
uv add voicegateway
# For the LiveKit integration path:
uv add "voicegateway[livekit]"
```

```bash pip
pip install voicegateway
# For the LiveKit integration path:
pip install "voicegateway[livekit]"
```

</CodeGroup>

VoiceGateway is framework-agnostic and no longer bundles provider or local-model wheels. Install the provider plugins your agent uses in your own agent (you likely already have them), for example `pip install livekit-plugins-openai livekit-plugins-deepgram livekit-plugins-cartesia`. For local runtimes, install them yourself: `pip install faster-whisper` (Whisper), `pip install kokoro-onnx onnxruntime` (Kokoro), or `pip install piper-tts` (Piper). VoiceGateway meters all of these instances by model_id via voice-prices, and meters `local/*` and `ollama/*` for free. `attach()`/`guard()` error messages point at the upstream wheel (for example `livekit-plugins-openai`), not a VoiceGateway extra.

## `attach`

```python
attach(
    session,
    *,
    project: str | None = None,
    agent_id: str | None = None,
    tenant_id: str | None = None,
    channel: str | None = None,
    collector_url: str | None = None,
    api_key: str | None = None,
    sink=None,
    room: str | None = None,
    heartbeat: bool = False,
    transcript: bool = True,
    snapshots: bool = False,
) -> str
```

`attach` wires a LiveKit `AgentSession` or Pipecat `PipelineTask` into the VoiceGateway middleware pipeline. It returns a session id string (`"vg-<uuid4>"`).

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session` | `AgentSession` or `PipelineTask` | required | The agent session or pipeline task to instrument. |
| `project` | `str \| None` | `None` (env `VOICEGW_PROJECT`, else `"default"`) | Project to bill this session against. |
| `agent_id` | `str \| None` | `None` (env `VOICEGW_AGENT_ID`, else hostname) | Human-readable agent identifier. Appears in the dashboard and logs. |
| `tenant_id` | `str \| None` | `None` | Per-call tenant identifier for multi-tenant cost slicing. |
| `channel` | `str \| None` | `None` | `"telephony"` or `"web"`. Auto-detected from the transport when omitted (a SIP participant on LiveKit, a telephony transport/serializer module on Pipecat). |
| `collector_url` | `str \| None` | `None` | Override the collector endpoint for this session. Falls back to `VOICEGW_COLLECTOR_URL`. |
| `api_key` | `str \| None` | `None` | Override the API key for this session. Falls back to `VOICEGW_API_KEY`. |
| `sink` | sink instance or `None` | `None` | Custom telemetry sink. Defaults to a local SQLite sink, or a `RemoteCollectorSink` when `collector_url` is set. |
| `room` | `str \| None` | `None` (auto-resolved from the LiveKit job context) | LiveKit room **name** (not a `Room` object), stamped on captured rows for probe correlation: `voicegw livekit latency` reads the STT/LLM/TTS split back by this name. Ignored on the Pipecat path. |
| `heartbeat` | `bool` | `False` | Register this process in the fleet roster and heartbeat its presence (see [`register_worker`](#register_worker) below). Best for single-process agents; the LiveKit process-executor model (`agent dev`/`start`) needs `register_worker` called once at `__main__` boot instead. |
| `transcript` | `bool` | `True` | Capture the call transcript on session close, from the framework's conversation history. Set `VOICEGW_TRANSCRIPTS=0` to disable fleet-wide (wins over the argument). LiveKit only; the Pipecat path accepts the flag but does not capture yet. |
| `snapshots` | `bool` | `False` | Capture conversation-state snapshots (system prompt, message history, tool calls) for `voicegw replay` and the dashboard's replay view. Set `VOICEGW_SNAPSHOTS=0` to force off fleet-wide. Requires a local sink (skipped against a remote collector). LiveKit only. See [Session replay](/cli/replay). |

### Usage

<Tabs>
  <Tab title="LiveKit">

```python
from livekit.agents import AgentSession, Agent, RoomInputOptions
from voicegateway import attach

class MyAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions="You are a helpful assistant.")

async def entrypoint(ctx):
    session = AgentSession()
    session_id = attach(
        session,
        project="my-app",
        agent_id="support-bot",
        tenant_id=ctx.room.name,  # optional per-call tenant
    )
    await session.start(
        agent=MyAgent(),
        room=ctx.room,
        room_input_options=RoomInputOptions(),
    )
```

  </Tab>
  <Tab title="Pipecat">

```python
from pipecat.pipeline.task import PipelineTask
from voicegateway import attach, Observer

task = PipelineTask(
    pipeline,
    observers=[Observer(project="my-app", tenant_id="acme")],
)
# Or use attach directly on the task after construction:
session_id = attach(task, project="my-app", tenant_id="acme")
```

  </Tab>
</Tabs>

### Return value

`attach` returns the session id string (`"vg-<uuid4>"`). Use it to correlate external logs with VoiceGateway cost rows.

```python
session_id = attach(session, project="my-app")
logger.info("session started", extra={"vg_session": session_id})
```

### Tenant attribution

Single-tenant is the default. Set `tenant_id` only when you need per-call attribution to a specific customer within one deployment: it is stamped directly on the rows `attach` writes for this session, no room or transport involved. The stamp is what the hosted cloud bills per tenant against; the OSS deployment stores it for SQL analysis.

```python
attach(session, project="acme-platform", tenant_id=request.user_id)
```

Sessions with no tenant set are stored as `tenant_id = NULL` (the single-tenant default) and appear as "unattributed" downstream.

See [Attach guide](/guide/attach) for a full walkthrough.

## `guard`

```python
guard(
    provider,
    *,
    fallback: tuple | list = (),
    rate_limit: str | None = None,
    budget: str | None = None,
    project: str | None = None,
) -> <same framework type as provider>
```

`guard` wraps a native provider instance with fallback chains, rate limiting, and per-project budget enforcement. It returns a drop-in wrapper of the same framework type as `provider` (a subclass of the matching LiveKit/Pipecat STT, LLM, or TTS base class), so it slots into an `AgentSession` or pipeline unchanged. `guard` writes no metrics itself; pair it with `attach(session)` for cost and latency, which never double-counts.

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `provider` | STT, LLM, or TTS plugin instance | required | The provider to wrap. |
| `fallback` | `tuple` or `list` | `()` | Ordered sequence of same-framework provider instances tried, in order, when the primary raises. `attach` stamps `fallback_from=<primary>` on the row for whichever provider actually ran. |
| `rate_limit` | `str \| None` | `None` | A DSL string: `"<count>/min"` or `"<count>/s"` (also accepts `sec`/`second`/`minute`), for example `"60/min"` or `"5/s"`. Internally normalized to a per-minute token bucket, so `"5/s"` becomes an average of 300 requests/min rather than a hard per-second ceiling. Raises `RateLimitExceeded` when the bucket is empty. |
| `budget` | `str \| None` | `None` | A DSL string: `"$<amount>/day"` or `"$<amount>/month"` (the `$` is optional), for example `"$5.00/day"`. Checked against the project's accumulated spend before each call. `day` reads the **rolling trailing 24 hours**, not calendar midnight; `month` reads the **rolling trailing 30 days**. Raises `BudgetExceededError` when spend is at or over the cap. |
| `project` | `str \| None` | `None` (defaults to `"default"`) | Project scope for the budget spend lookup. |

### Errors

`rate_limit` and `budget` are parsed eagerly: a string that does not match the DSL (for example `rate_limit="60"` with no unit) raises `ValueError` at `guard()` call time, not at request time.

At call time, a guarded provider can raise:

| Exception | Import | Raised when |
|---|---|---|
| `RateLimitExceeded` | `voicegateway.middleware.rate_limiter_middleware.RateLimitExceeded` | The token bucket is empty. |
| `BudgetExceededError` | `voicegateway.middleware.budget_enforcer_middleware.BudgetExceededError` | The project's window spend is at or over the `budget` cap. Carries `.project`, `.spent_usd`, `.budget_usd`. |

Both subclass `voicegateway.middleware.base_middleware.MiddlewareError`.

### Usage

<Tabs>
  <Tab title="LiveKit">

```python
from livekit.agents import AgentSession
from livekit.plugins import deepgram, openai, cartesia
from voicegateway import attach, guard

async def entrypoint(ctx):
    session = AgentSession(
        stt=guard(
            deepgram.STT(model="nova-3"),
            fallback=[openai.STT()],
            rate_limit="60/min",
        ),
        llm=guard(
            openai.LLM(model="gpt-4o-mini"),
            fallback=[openai.LLM(model="gpt-4.1-nano")],
            budget="$5.00/day",
            project="my-app",
        ),
        tts=guard(
            cartesia.TTS(model="sonic-3"),
            fallback=[openai.TTS()],
        ),
    )
    attach(session, project="my-app")
    await session.start(agent=MyAgent(), room=ctx.room)
```

  </Tab>
  <Tab title="Pipecat">

```python
from pipecat.services.deepgram import DeepgramSTTService
from pipecat.services.openai import OpenAILLMService
from voicegateway import guard

stt = guard(
    DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY"]),
    fallback=[DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY_BACKUP"])],
    rate_limit="60/min",
)
```

  </Tab>
</Tabs>

### Return value

`guard` returns a wrapper of the same framework type as `provider`. Provider calls pass through to the underlying implementation, then to the fallback chain on error.

See [Guard guide](/guide/guard) for fallback chain behavior and error handling.

## `register_worker`

```python
register_worker(
    agent_name: str,
    *,
    project: str = "default",
    tenant_id: str | None = None,
    collector_url: str | None = None,
    api_key: str | None = None,
    region: str | None = None,
    version: str | None = None,
    interval: float = 15.0,
    local: bool = False,
    db_path: str | None = None,
    dispatch_name: str | None = "<unset: defaults to agent_name>",
) -> str
```

Registers this process as an agent worker and starts heartbeating its presence, so it shows in the dashboard's Fleet/Agents view (idle or busy) even before it has handled a call. Returns the agent id.

Call once at worker boot. Two transports, chosen by whether a collector is configured:

- **Collector mode** (`collector_url` or `VOICEGW_COLLECTOR_URL` set): an asyncio task pushes presence to the collector's `POST /v1/agents/heartbeat`. Needs a running event loop.
- **Local mode** (`local=True`, no collector): a background thread writes presence straight to the shared SQLite (`db_path` / `VOICEGW_DB_PATH` / the default), which the co-located dashboard reads. Needs no event loop, so a worker registered in a plain `__main__` block is visible while idle immediately.

Without a collector and without `local=True`, the worker is tracked in-process but nothing is pushed anywhere.

`dispatch_name` is the LiveKit `agent_name` this worker dispatches under, which the dashboard's probe button uses to place a call by name. Left unset, it defaults to `agent_name`. Pass an explicit `None` for a worker with no LiveKit dispatch (a Pipecat agent), which keeps it in the roster but not probeable by name.

### One writer per agent identity

In the LiveKit process-executor model (`agent dev` / `agent start`), the worker is the **main process** and per-call work runs in spawned job subprocesses. Call `register_worker("agent", local=True)` once in your `__main__` block, and do **not** also pass `attach(heartbeat=True)` in the job: the subprocess would become a second writer of the same roster row. `attach(heartbeat=True)` is for single-process agents (Pipecat, or the LiveKit thread executor), where `attach` is the sole writer.

```python
# __main__ boot, before agents.cli.run_app(...)
from voicegateway import register_worker

register_worker("reception", local=True)
```

## `Observer`

`Observer` is the Pipecat integration class. Pass it to `PipelineTask(observers=[...])` to wire VoiceGateway cost tracking and metrics capture into a Pipecat pipeline.

```python
from voicegateway import Observer
from pipecat.pipeline.task import PipelineTask

task = PipelineTask(
    pipeline,
    observers=[
        Observer(
            project="my-app",
            agent_id="support-bot",
            tenant_id="acme",
            collector_url="https://ingest.voicegateway.dev",
            api_key="vk_...",
        )
    ],
)
```

### Constructor parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project` | `str` | `"default"` | Project to bill this pipeline run against. |
| `agent_id` | `str \| None` | `None` | Human-readable agent identifier. |
| `tenant_id` | `str \| None` | `None` | Per-call tenant identifier. |
| `collector_url` | `str \| None` | `None` | Collector endpoint. Falls back to `VOICEGW_COLLECTOR_URL`. |
| `api_key` | `str \| None` | `None` | API key. Falls back to `VOICEGW_API_KEY`. |

`Observer` implements the Pipecat `BaseObserver` protocol. It captures per-frame STT, LLM, and TTS events and flushes them to the VoiceGateway collector on pipeline shutdown.

<Tip>
For LiveKit agents, use `attach` instead of `Observer`. `Observer` is the Pipecat-specific integration path.
</Tip>

## `__version__`

```python
from voicegateway import __version__

print(__version__)  # e.g. "0.3.1"
```

The installed package version string. Useful for logging and support diagnostics.

## Where to go next

| You want to | Use this |
|---|---|
| Full attach walkthrough | [Guide: attach](/guide/attach) |
| Full guard walkthrough | [Guide: guard](/guide/guard) |
| List projects via CLI | [CLI reference](/cli/index) |
| Query costs via REST | [HTTP API](/api/http-api) |
| Integrate with AI coding agents | [MCP server](/mcp/index) |
