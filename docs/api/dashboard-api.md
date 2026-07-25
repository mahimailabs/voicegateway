---
title: Dashboard API Reference
description: Read-only `/api/*` endpoints served by the daemon alongside the HTTP API and the React SPA. Consumed automatically by the built-in web dashboard.
---

The dashboard API is mounted by the daemon (`voicegw serve`) under the `/api/` prefix on the same port as the public HTTP API (`/v1/*`) and the React SPA (`/`). The default port is 8080; the `serve.port` key in `voicegw.yaml` overrides it.

Start the daemon (the dashboard API ships with it):

```bash
voicegw serve
```

<Note>
These endpoints are optimized for the dashboard UI and aggregate data slightly differently from the HTTP API (`/v1/*`). For example, `/api/overview` combines multiple queries into one response. If you are building external tooling, prefer the [HTTP API](/api/http-api).
</Note>

## GET /api/status

Returns the configuration status of all providers, registered models, and fallback chains.

**Response:**

```json
{
  "providers": {
    "deepgram": { "configured": true, "type": "cloud" },
    "openai": { "configured": true, "type": "cloud" },
    "whisper": { "configured": true, "type": "local" }
  },
  "models": {
    "deepgram/nova-3": { "modality": "stt", "provider": "deepgram" },
    "openai/gpt-4o-mini": { "modality": "llm", "provider": "openai" }
  },
  "fallbacks": {
    "stt": ["deepgram/nova-3", "local/whisper-large-v3"],
    "llm": ["openai/gpt-4o-mini", "groq/llama-3.3-70b-versatile"],
    "tts": ["cartesia/sonic-3", "local/kokoro"]
  }
}
```

**Example:**

```bash
curl http://localhost:8080/api/status
```

## GET /api/overview

Return aggregated dashboard overview statistics. This endpoint combines multiple queries into a single response for the dashboard's summary cards.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project` | `string` | `null` | Filter all stats by project ID. |

**Response:**

```json
{
  "total_requests": 12450,
  "total_cost_today": 15.23,
  "total_cost_all": 342.87,
  "active_models": 8,
  "providers_configured": 5
}
```

**Example:**

```bash
curl http://localhost:8080/api/overview
curl "http://localhost:8080/api/overview?project=tonys-pizza"
```

## GET /api/costs

Return cost summary for a period, optionally filtered by project. Includes per-project breakdown when no project filter is applied.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `period` | `string` | `"today"` | One of: `today`, `week`, `month`, `all`. |
| `project` | `string` | `null` | Filter by project ID. |

**Response:**

```json
{
  "period": "today",
  "total": 3.4521,
  "by_provider": {
    "deepgram": { "cost": 1.2000, "requests": 85 },
    "openai": { "cost": 2.2521, "requests": 42 }
  },
  "by_model": {
    "deepgram/nova-3": { "cost": 1.2000, "requests": 85 },
    "openai/gpt-4o-mini": { "cost": 2.2521, "requests": 42 }
  },
  "by_project": {
    "tonys-pizza": { "cost": 2.1000, "requests": 90 },
    "sushi-bot": { "cost": 1.3521, "requests": 37 }
  }
}
```

**Example:**

```bash
curl "http://localhost:8080/api/costs?period=week"
curl "http://localhost:8080/api/costs?period=today&project=tonys-pizza"
```

## GET /api/latency

Return latency statistics, optionally filtered by project.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `period` | `string` | `"today"` | One of: `today`, `week`. |
| `project` | `string` | `null` | Filter by project ID. |

**Response:** Per-model latency statistics including average TTFB and total latency.

**Example:**

```bash
curl "http://localhost:8080/api/latency?period=today"
curl "http://localhost:8080/api/latency?project=my-app"
```

## GET /api/agents

Return the fleet index over the last 24 hours: the telemetry rollup merged with the live worker roster. Agents that have metered traffic come from the rollup; registered-but-idle workers (0 requests) are merged in so a booted agent appears before it has handled its first call.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | `integer` | `50` | Number of agents to return (1-1000). |
| `q` | `string` | `null` | Substring match against `agent_id`. |

**Response:**

```json
{
  "agents": [
    {
      "agent_id": "order-taker",
      "agent_name": "order-taker",
      "request_count": 412,
      "total_cost_usd": 1.8421,
      "last_seen": 1752460800.0,
      "error_rate": 0.0,
      "p95_latency_ms": 940.0,
      "memory_pct": 38.4,
      "models": {
        "stt": "deepgram/nova-3",
        "llm": "openai/gpt-4o-mini",
        "tts": "cartesia/sonic-3"
      },
      "latency_ms": { "stt": 210.0, "llm": 430.0, "tts": 180.0 },
      "fleet_status": "idle",
      "probe": {
        "eligible": true,
        "dispatch_name": "order-taker",
        "mode": "explicit",
        "reason": null
      }
    }
  ],
  "unattributed": {
    "request_count": 0,
    "total_cost_usd": 0.0,
    "last_seen": null,
    "error_rate": 0.0
  }
}
```

`fleet_status` is `idle`, `busy`, or `offline` from the heartbeat roster, and `null` when the agent is telemetry-only (not a currently registered worker). `latency_ms` holds the average first-byte latency per modality over the same 24-hour window; only STT, LLM, and TTS are metered, so the waterfall shows three segments and no more.

`probe` reports whether this agent's card can place a probe:

| Field | Type | Description |
|---|---|---|
| `eligible` | `boolean` | Whether `POST /api/agents/{agent_id}/probe` will be accepted. |
| `dispatch_name` | `string \| null` | The LiveKit `agent_name` VoiceGateway will dispatch by. `""` means automatic dispatch. `null` when no name is available: nothing was observed and no live worker is in the roster, or LiveKit is not configured on this host. `reason` says which. |
| `mode` | `"explicit" \| "automatic" \| null` | `explicit` dispatches to that name; `automatic` means creating the room is the whole dispatch. |
| `reason` | `string \| null` | Why the probe is unavailable, or a caveat on an eligible one (a name taken from the roster but not yet confirmed by a completed call, or more than one worker registered on automatic dispatch so whichever is online may answer). |

The dispatch name is LiveKit's `agent_name` (the value on `@server.rtc_session(agent_name=...)`, or the legacy `WorkerOptions.agent_name`). VoiceGateway resolves it from two sources, most-trusted first: the name **observed** on a call the agent already ran, and failing that the **dispatch name a live worker reports** in the fleet roster (`register_worker`'s name, or the value `attach` resolves from the job). The roster fallback lets a booted-but-idle agent be probed before its first call. A roster name is what the worker claimed, not what a finished job proved, so `reason` flags it as unverified; if it is wrong, the probe reaches no worker and returns that as an error rather than a fabricated number. A worker with no LiveKit dispatch (a Pipecat agent) reports no dispatch name and is not probeable, so it never gets a play button it could not answer. Only an agent absent from **both** sources comes back ineligible.

**Example:**

```bash
curl http://localhost:8080/api/agents
curl "http://localhost:8080/api/agents?q=order&limit=10"
```

## POST /api/agents/{agent_id}/probe

Place one real call to this agent and report its latency split and cost.

Every press is billed traffic against the agent's real providers, so this endpoint is admin-scoped (`admin`; the gate is a no-op until API keys are configured) and rate limited per agent: one probe in flight at a time, and no more often than every 30 seconds. Limits are per agent, so probing one agent never throttles another.

One press places exactly one call. There is no warmup turn: a discarded warm-up would double what the press charges while `cost_usd` reported only half of it. The sample therefore includes whatever cold start that one call hit, which is why the dashboard renders it beside the 24-hour average rather than merging the two.

Probe rows are tagged by a `vg-probe-` room name, which the 24-hour rollups exclude. Pressing play cannot move the agent's own cost, p95, or error rate.

**Response:**

```json
{
  "agent_id": "order-taker",
  "dispatch_name": "order-taker",
  "mode": "explicit",
  "room": "vg-probe-order-taker-1a2b3c4d",
  "trials": 1,
  "e2e": { "avg": 1.24, "p50": 1.24, "p95": 1.24, "min": 1.24, "max": 1.24, "trials": 1 },
  "components": { "stt": 0.21, "llm_ttft": 0.43, "tts": 0.18 },
  "cost_usd": 0.0031,
  "error": null
}
```

<Warning>
All probe times are **seconds**, not milliseconds. The rest of this API reports milliseconds.
</Warning>

Every number returned was measured: `e2e` comes from a synthetic client that speaks a fixed utterance and waits for audio back, while `components` and `cost_usd` are read from the rows the agent itself wrote for the probe's room. Anything that could not be measured is `null`, never zero. A `null` `cost_usd` means this host cannot know (the agent ships its telemetry to a remote collector), which is a different claim from "the call was free". `components` is `null` for the same reason. `e2e` is `null` when no turn completed, in which case `error` says why. Two failure shapes are named rather than left blank: if the dispatch reached no worker (a wrong or unverified name, or an offline automatic worker), the probe detects that no one joined the room and `error` says so; and if the agent joined but its own pipeline errored (an STT/LLM/TTS that failed, for example a `401` to a model gateway), `error` carries the agent's own message (e.g. `STT: Invalid response status (401 Unauthorized)`), read back from the error rows it wrote, so a probe that measured nothing still says why.

`components` carries whichever of these legs the call produced: `eou` (turn detection), `stt`, `stt_ttfp` (onset to first partial, the head), `stt_transcription_delay` (end of speech to final, the tail), `llm_ttft`, and `tts`. Keys for legs the call did not produce are absent rather than zeroed.

**Errors:**

| Status | Condition |
|---|---|
| `400` | Telemetry storage is disabled, LiveKit is not configured, or the agent has no dispatch name in either source (no observed job and no live worker in the roster). |
| `409` | A probe for this agent is already running. |
| `429` | Inside the 30-second cooldown. Carries a `Retry-After` header. |
| `504` | The probe did not finish within 120 seconds. |

**Example:**

```bash
curl -X POST http://localhost:8080/api/agents/order-taker/probe \
  -H "Authorization: Bearer vk_..."
```

## GET /api/projects

List all configured projects with today's stats.

**Response:**

```json
{
  "projects": [
    {
      "id": "tonys-pizza",
      "name": "Tony's Pizza",
      "description": "Pizza ordering voice agent",
      "daily_budget": 10.0,
      "default_stack": "premium",
      "tags": ["production"],
      "accent": "#e74c3c"
    }
  ],
  "stats": {
    "tonys-pizza": {
      "cost_today": 2.45,
      "requests_today": 120
    }
  }
}
```

**Example:**

```bash
curl http://localhost:8080/api/projects
```

## Static File Serving

The dashboard also serves the React frontend's built assets. If the frontend has been built (`src/dashboard/frontend/dist/` exists), the daemon serves:

- `GET /` -- the React app's `index.html`
- `GET /assets/*` -- bundled JavaScript, CSS, and other static files
- All other paths fall through to `index.html` for client-side routing (SPA fallback)

If the frontend has not been built, `GET /` returns an error message with build instructions.
