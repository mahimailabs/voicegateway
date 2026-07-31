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

## GET /api/calls

Return recent recorded calls, newest first, each with its participant legs. A call row is written by the LiveKit webhook receiver and by agent/load-worker self-reports (`POST /v1/calls/observations`), so a call that ran no inference at all still appears here. This is what the dashboard's per-call layer waterfall reads.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | `integer` | `50` | Page size, 1-200. Each call costs one extra read for its legs, so the cap bounds the query count too. |

**Response:**

```json
{
  "calls": [
    {
      "id": "8f1c...",
      "room_sid": "RM_abc123",
      "room_name": "call-inbound-42",
      "origin": "webhook",
      "attempt_id": null,
      "run_id": null,
      "project": "default",
      "tenant_id": null,
      "agent_id": null,
      "channel": "sip",
      "direction": "inbound",
      "started_at_ms": 1750000000000,
      "ended_at_ms": 1750000042000,
      "duration_ms": 42000,
      "end_reason": "CLIENT_INITIATED",
      "num_legs": 2,
      "is_probe": 0,
      "answer_latency_ms": 1200,
      "answer_latency_source": "webhook_proxy",
      "legs": [
        {
          "id": 1,
          "call_id": "8f1c...",
          "participant_sid": "PA_caller",
          "identity": "sip_+15195551234",
          "kind": "SIP",
          "region": null,
          "joined_at_ms": 1750000000000,
          "left_at_ms": 1750000042000,
          "disconnect_reason": "CLIENT_INITIATED",
          "is_publisher": 1,
          "attributes_json": "{\"sip.phoneNumber\": \"+15195551234\"}",
          "first_audio_track_at_ms": null,
          "audio_track_sid": null,
          "audio_codec": null,
          "joined_at_source": "webhook",
          "first_audio_track_at_source": null
        }
      ]
    }
  ]
}
```

`answer_latency_ms` is the caller-visible ring time. It is derived once, at write time, and served here untouched. `answer_latency_source` names the clock behind it, strongest first:

| Source | Meaning |
|---|---|
| `sipp_rtd` | The true INVITE-to-200-OK wall time, measured and reported by the worker that placed the call. |
| `agent_report` | Derived from two leg timestamps that were both self-reported by a process inside the call: millisecond precision. |
| `webhook_proxy` | The same subtraction from LiveKit webhook timestamps, whose `created_at` is whole seconds. Carries up to a second of truncation. |

Four things this endpoint deliberately does not do:

- **It never computes.** No value is re-derived, repaired or aggregated on the way out, and no percentile is served: one page of rows is not the population.
- **It never fills a NULL.** `answer_latency_ms: null` means the recorded timestamps do not support a ring time (for example a call that never answered), which is a different fact from a fast answer. Nothing is coerced to `0` or `""`.
- **It excludes load-test traffic.** Rows flagged `is_probe` are synthetic and are not served here, so they cannot leak into numbers read as production.
- **It is polled, not pushed.** There is no WebSocket or SSE variant.

Only the `sip.*` participant attributes are persisted and served in `attributes_json`; other participant attributes are application state and are dropped at write time.

Authentication follows the other dashboard reads: with no API keys configured (the self-hosted default) the endpoint is open, and once keys are configured a tenant-scoped key sees only its own calls.

Tenant scoping happens in the query, not on the page after it is read, so `limit` means the same thing for a scoped key as for the operator: a key bound to one tenant asking for 50 gets its own newest 50, not whatever fraction of the newest 50 calls overall happened to belong to it. A key whose `tenant_id` is null reads the unattributed calls (`tenant_id IS NULL`), never every tenant's.

**Example:**

```bash
curl "http://localhost:8080/api/calls?limit=6"
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

## GET /api/diagnostics/runs/{run_id}/report

Export one stored diagnostics run as a versioned JSON payload. Requires the `admin` scope, like every other diagnostics endpoint, because a run can place billed calls.

The payload carries `schema_version` (currently `1`) and `kind: "voicegateway.diagnostics.run_report"`. Within a major version the payload is **additive only**: no key changes meaning, type or nesting, none disappears, and parsers must ignore keys they do not recognise. Anything that would break a v1 parser ships as `schema_version: 2`.

Unmeasured is `null`, never `0`. A check that was not part of the run, one that recorded no result, and one that errored are three distinct states, so an absent measurement can never be read as a clean one. Packet loss is reported as the literal `"not_measured"` because it is not observable server-side.

The run's verdict is read from what the run stored, not recomputed: `livekit_diag/gates.py` is the only place in the product that decides a verdict. A run recorded before gates existed reports `gates_recorded: false` rather than being re-judged after the fact.

## GET /api/diagnostics/runs/{run_id}/report.html

The same report as a **single self-contained HTML file**, served with `Content-Disposition: attachment`. Requires the `admin` scope.

Self-contained means exactly that: no script, no external stylesheet, no remote font, no image, no network request of any kind. It renders correctly from `file://` on a machine with no internet, months later, which is the point of handing it to a client or attaching it to a ticket.

A verdict of `UNKNOWN` renders as the word `UNKNOWN` on neutral grey with the line "This is NOT a pass", never a green tick. Each gate prints its status as text rather than colour alone, so a printed or monochrome copy still carries it.

## Static files

- `GET /` -- the React app's `index.html`
- `GET /assets/*` -- bundled JavaScript, CSS, and other static files
- All other paths fall through to `index.html` for client-side routing (SPA fallback)

If the frontend has not been built, `GET /` returns an error message with build instructions.
