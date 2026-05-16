# Python SDK Reference

VoiceGateway exposes one public Python surface: the
`voicegateway.inference` module, a drop-in mirror of
`livekit.agents.inference`. New agent code uses it; existing LiveKit
Cloud Inference code switches over with one import-line change.

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

The `model` string parses as `provider/model[:language]`. Provider names are validated against the eleven supported types (`openai`, `deepgram`, `cartesia`, `anthropic`, `groq`, `elevenlabs`, `assemblyai`, `ollama`, `whisper`, `kokoro`, `piper`). The `api_key` kwarg, when given, overrides the project's resolved key for this one instance (useful for testing).

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

Same shape as STT, plus a `voice` kwarg. The trailing colon-suffix in the model string parses as voice (NOT language). That is the semantic asymmetry between STT and TTS that LiveKit defines.

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
    tenant_id: str | None = None,   # v0.4.0
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

## Tenant attribution (v0.4.0)

VoiceGateway tags each session with an optional `tenant_id` so multi-tenant operators can slice costs, metrics, and replay by customer. The tenant flows through three independent surfaces; pick the one that matches your deployment.

### 1. `attach_session(..., tenant_id="…")`

The opt-in path. Pass `tenant_id` at the same time you wire the LiveKit `AgentSession`, and every cost row, metric row, and replay event from that session lands tagged.

```python
from voicegateway import inference

async def handle_call(tenant_id: str):
    agent_session = AgentSession(...)
    inference.attach_session(agent_session, tenant_id=tenant_id)
    await agent_session.start(...)
```

When `tenant_id` is omitted (default `None`) the ContextVar is left alone, so a virtual key resolved earlier in the request (see surface 3 below) or an explicit `set_tenant(...)` call still wins. Calling with `tenant_id=None` does not clear a previously-set scope.

### 2. `inference.set_tenant(tenant_id)`

The escape hatch for code that does not own the `AgentSession` construction. Sets the `tenant_id_ctx` ContextVar for the rest of the async context; the next `log_request` call picks it up and stamps the session row. 128-char UTF-8 cap (locked at v0.4.0 OQ2).

```python
from voicegateway import inference

inference.set_tenant("acme")
stt = inference.STT("deepgram/nova-3")
# ... subsequent factories inherit the tenant via the ContextVar.
```

`inference.current_tenant()` reads the current scope without modifying it. `inference.reset_tenant_id()` clears the ContextVar for a new session boundary inside the same task.

### 3. Virtual API keys (no code change required)

When a request authenticates with a `vk_`-prefixed virtual key whose `tenant_id` is set, the HTTP API's auth middleware (in `src/voicegateway/server/main.py::build_app`) auto-tags the session with the key's scope. Agent code does not need to know the tenant: the dashboard's Virtual Keys page (`/virtual-keys`) issues a scoped key, the operator ships it as `Authorization: Bearer vk_…`, and every request inherits the scope.

Body-level `tenant_id` is rejected with `403` when it conflicts with a scoped virtual key. Unscoped virtual keys (issued without a tenant) allow the body to declare any tenant, matching the static-key behavior.

### The "unattributed" bucket

Sessions where none of the three surfaces set a tenant get `tenant_id = NULL` in storage. The dashboard renders these as a muted "unattributed" pill rather than a literal tenant string. No backfill happens at migration time (OQ3 locked at v0.4.0/T01): pre-v0.4.0 rows stay NULL and the FE handles them gracefully.

The first tenant-bearing request "wins" for the session's lifetime. A later unattributed request on the same `session_id` does not clear the tenant_id (the `sessions` UPSERT uses `COALESCE(tenant_id, excluded.tenant_id)`).

For the operator-facing workflow (issuing keys, viewing per-tenant costs, exporting), see the [multi-tenant quickstart](/guide/multi-tenant-quickstart).

## Cross-modality routing (v0.5.0)

Each project carries a latency budget and a per-modality provider roster. When a session starts, VoiceGateway picks the (STT, LLM, TTS) combination from the roster that minimises predicted total latency under the budget. The pick is recorded on the session row so the dashboard can show what ran and how close the call landed to the budget.

```yaml
projects:
  acme:
    name: Acme
    routing:
      budget_ms: 1500            # OQ1 lock: typical conversational target.
      fallback_to_fastest: true  # When no triple fits, pick the fastest and flag budget_overrun.
      rosters:
        stt: [deepgram, assemblyai]    # Ordered by operator preference.
        llm: [groq, openai, anthropic]
        tts: [cartesia, elevenlabs]
```

### How the router picks

At session start, the router reads three inputs and produces a `RoutedTriple` plus a `budget_overrun` boolean.

1. **Observed p50 per (provider, modality)**: rolled up by the 15-minute worker from the requests table, written to `latency_observations`. The router prefers observed data when present.
2. **Curated published-median baselines** in `src/voicegateway/core/provider_baselines.json` (REQ-VG-ROUTE-002 AC-4 fallback). Used when no observation exists for a candidate. Operators can edit the JSON to update a published median or add a missing provider.
3. **Caller overrides**: explicit `{modality: provider}` map passed from the agent code. The router respects overrides for the named modalities and only picks for the unset ones (AC-3).

Candidate triples are the cartesian product of the rosters minus the overridden modalities. The router computes a predicted total (sum of per-modality predictions), picks the lowest one whose total fits the budget. If nothing fits and `fallback_to_fastest=true`, it picks the fastest available and flags `budget_overrun=true`. If `fallback_to_fastest=false`, it raises `BudgetExceeded`.

### Explicit overrides from agent code

Pass the caller-override dict through whatever surface attaches the session. The v0.5.0 reference path is `route_session(...)` returning a `RoutedTriple`, with the caller then handing the triple to `attach_session(routed_triple=...)`:

```python
from voicegateway import inference
from voicegateway.middleware import router

async def handle_call(project_id: str, caller_overrides: dict[str, str] | None = None):
    db = await gateway.storage._ensure_initialized()
    triple = await router.route_session(
        db,
        project_id=project_id,
        project_config=gateway.config.projects[project_id],
        caller_overrides=caller_overrides,
    )
    agent_session = AgentSession(...)
    inference.attach_session(
        agent_session,
        routed_triple=(triple.stt, triple.llm, triple.tts, triple.predicted_ms, triple.budget_overrun),
    )
    await agent_session.start(...)
```

The router runs once per session; the picked triple is immutable for the session's lifetime (AC-5).

### Inspecting what the router would pick

For ops debugging, `voicegw route show <project>` prints the current observations and rosters, and `voicegw route simulate <project> [--stt X] [--llm Y]` dry-runs the picker without writing a session row. Both accept `--json` for scripting.

For the agency-facing operator workflow (tuning budgets, uploading branding, exporting per-project data), see the [agency quickstart](/guide/agency-quickstart).

## Voice-specific guardrails (v0.6.0)

Guardrails are project-scoped and injected through the existing drop-in `voicegateway.inference.LLM(...)` path. No separate session-create service is required.

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

On the first guarded LLM chat in a session, VoiceGateway freezes the active policy, appends a versioned guardrail system block after existing system/developer instructions, and registers the reserved LiveKit tool `report_guardrail_action(category, action, context_excerpt)`. User-defined tools with that name are rejected when guardrails are active.

Bypass is explicit and audited:

```python
from voicegateway import inference

inference.start_session(bypass_guardrails=True)

# Or when binding a custom LiveKit AgentSession:
inference.attach_session(agent_session, bypass_guardrails=True)
```

Bypass skips prompt/tool injection for that session and writes a `bypassed` audit row when a policy would otherwise be active. See the [guardrails guide](/guide/guardrails) and [prompt reference](/reference/guardrail-prompts).

## Conversation replay capture (v0.3.0)

VoiceGateway captures a per-event timeline for every voice conversation: each STT chunk, each LLM token, each TTS frame, plus periodic conversation-state snapshots. The dashboard's [Replay page](/) then scrubs through any past call moment-by-moment with cost accruing live. This happens automatically; users do not call any function to opt in.

The capture path runs alongside the v0.2.0 metrics pipeline. The same `attach_session` helper covered above wires replay events into the [`ReplayCapture`](https://github.com/mahimailabs/voicegateway/blob/main/voicegateway/middleware/replay_capture.py) buffer on the standard worker pattern. Custom AgentSession subclasses use the same opt-in escape hatch.

### Defaults and per-project knobs

Replay capture defaults live under each project's `replay:` block in `voicegw.yaml`:

```yaml
projects:
  acme:
    name: Acme Corp
    replay:
      enabled: true             # capture for every session in this project
      retention_days: 90        # age replay rows out after this window
      buffer_size_events: 5000  # per-session in-memory cap before dropping oldest
      flush_size_events: 500    # batched writes to storage every N events
```

All four fields are optional; omitting `replay:` accepts the Foundry-locked defaults shown above. The `enabled` toggle disables capture for the project (cost and metrics aggregates continue as before); the other three tune the storage/memory trade-off documented in [docs/storage/replay-storage-costs.md](/storage/replay-storage-costs).

### Disabling capture

For projects that should not record replay (sensitive content, regulatory constraint, storage cost concerns), set `enabled: false`:

```yaml
projects:
  high-pii-project:
    name: Sensitive Workflow
    replay:
      enabled: false
```

No replay events are captured for sessions in that project; the dashboard's Replay page renders the [pre-v0.3.0 banner](https://github.com/mahimailabs/voicegateway/blob/main/dashboard/frontend/src/components/replay/PreV030Banner.tsx) ("recorded before replay capture existed") with a link out to the per-modality session detail. Cost tracking, latency, and the v0.2.0 metrics view continue uninterrupted.

### Retention worker

The [`RetentionWorker`](https://github.com/mahimailabs/voicegateway/blob/main/voicegateway/storage/retention_worker.py) runs once an hour as a background asyncio task; it reads each project's `retention_days` and deletes replay rows tied to sessions whose `ended_at` is older than the window. Single-process for v0.3.0; multi-replica coordination is out of scope.

The dashboard's `POST /api/projects/{id}/replay/retention` endpoint updates `retention_days` in memory for the current process. The change applies on the next worker tick. Persistence to `voicegw.yaml` on disk is a v0.3.x follow-up; restarting the gateway reverts to the file-defined value.

## Operations: where to go

| You want to | Use this |
|---|---|
| List projects | `voicegw projects` (CLI), `GET /v1/projects` (HTTP), `list_projects` (MCP) |
| See costs | `voicegw costs` (CLI), `GET /v1/costs` (HTTP), `get_costs` (MCP), the dashboard |
| Tail recent requests | `voicegw logs` (CLI), `GET /v1/logs` (HTTP), `get_logs` (MCP) |
| Add or rotate a provider key | `vg_add_provider` / `vg_set_provider_key` (MCP), the dashboard Providers page |
| Reconcile against an invoice | `voicegw reconcile --provider <name> --provider-usage-file <path>` |

The Python SDK does not include these helpers; they live in the surfaces above.
