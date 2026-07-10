---
title: Python SDK Reference
description: Public Python API for attaching VoiceGateway cost tracking and fallback to LiveKit and Pipecat agents.
---

The public Python surface is four names exported from the top-level `voicegateway` package: `attach`, `guard`, `Observer`, and `__version__`.

```python
from voicegateway import attach, guard, Observer, __version__
```

Cost queries, project management, latency stats, and request logs live outside the Python SDK. Use the [CLI](/cli/index), the [HTTP API](/api/http-api), the [Dashboard API](/api/dashboard-api), or the [MCP tools](/mcp/index) for those.

<Note>
The inference factories (`STT`, `LLM`, `TTS`) from the deprecated `inference` submodule are being removed. If you are migrating from them, see [Migrating from inference factories](/guide/migration-attach-guard).
</Note>

## Installation

<CodeGroup>

```bash uv
uv add voicegateway
# With provider extras:
uv add "voicegateway[openai,deepgram,cartesia]"
```

```bash pip
pip install voicegateway
# With provider extras:
pip install "voicegateway[openai,deepgram,cartesia]"
```

</CodeGroup>

## `attach`

```python
attach(
    target,
    *,
    project: str = "default",
    agent_id: str | None = None,
    tenant_id: str | None = None,
    channel: str | None = None,
    collector_url: str | None = None,
    api_key: str | None = None,
    sink=None,
    room=None,
) -> str
```

`attach` wires a LiveKit `AgentSession` or Pipecat `PipelineTask` into the VoiceGateway middleware pipeline. It returns a session id string (`"vg-<uuid4>"`).

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `target` | `AgentSession` or `PipelineTask` | required | The agent session or pipeline task to instrument. |
| `project` | `str` | `"default"` | Project to bill this session against. |
| `agent_id` | `str \| None` | `None` | Human-readable agent identifier. Appears in the dashboard and logs. |
| `tenant_id` | `str \| None` | `None` | Per-call tenant identifier for multi-tenant cost slicing. 128-char UTF-8 cap. |
| `channel` | `str \| None` | `None` | Logical channel label (for example, `"inbound"`, `"outbound"`). |
| `collector_url` | `str \| None` | `None` | Override the collector endpoint for this session. Falls back to `VOICEGW_COLLECTOR_URL`. |
| `api_key` | `str \| None` | `None` | Override the API key for this session. Falls back to `VOICEGW_API_KEY`. |
| `sink` | sink instance or `None` | `None` | Custom telemetry sink. Defaults to the process-level sink. |
| `room` | LiveKit `Room` or `None` | `None` | LiveKit room for metadata propagation (optional, used for tenant carry-on-wire). |

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

Set `tenant_id` to attribute this session to a specific customer. When `room` is also passed, VoiceGateway carries the tenant id in LiveKit room metadata so it survives SFU hops.

```python
attach(session, project="acme-platform", tenant_id=request.user_id, room=room)
```

Sessions with no tenant set are stored as `tenant_id = NULL` and shown as "unattributed" in the dashboard.

See [Attach guide](/guide/attach) for a full walkthrough.

## `guard`

```python
guard(
    provider,
    *,
    fallback: tuple | list = (),
    rate_limit: int | None = None,
    budget: float | None = None,
    project: str | None = None,
) -> <same type as provider>
```

`guard` wraps a provider instance with fallback chains, rate limiting, and per-project budget enforcement. It returns the same type as the input `provider`, so it is a transparent drop-in.

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `provider` | STT, LLM, or TTS plugin instance | required | The provider to wrap. |
| `fallback` | `tuple` or `list` | `()` | Ordered sequence of fallback provider instances tried on error or timeout. |
| `rate_limit` | `int \| None` | `None` | Maximum requests per minute. Excess requests block until capacity recovers. |
| `budget` | `float \| None` | `None` | Hard per-session cost ceiling in USD. Raises `BudgetExceeded` when crossed. |
| `project` | `str \| None` | `None` | Project scope for budget tracking. Defaults to the session's active project. |

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
            rate_limit=60,
        ),
        llm=guard(
            openai.LLM(model="gpt-4o-mini"),
            fallback=[openai.LLM(model="gpt-4.1-nano")],
            budget=0.05,
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
    rate_limit=60,
)
```

  </Tab>
</Tabs>

### Return value

`guard` returns the same type as `provider`. The returned object is a transparent proxy: all provider method calls pass through to the underlying implementation and then to the fallback chain on error.

See [Guard guide](/guide/guard) for fallback chain behavior, budget enforcement details, and error handling.

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
| Migrate from inference factories | [Migration guide](/guide/migration-attach-guard) |
| List projects via CLI | [CLI reference](/cli/index) |
| Query costs via REST | [HTTP API](/api/http-api) |
| Integrate with AI coding agents | [MCP server](/mcp/index) |
