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

## GET /api/correlation

How many sessions that had a room actually reached a call row. This is a data-quality reading about VoiceGateway's own recording, not about your deployment's health: a low rate means calls are being missed or arriving unmatched, so any per-call number computed downstream is drawn from an incomplete set.

**Response:**

```json
{
  "eligible": 6,
  "correlated": 4,
  "rate": 0.6666666666666666,
  "ambiguous": 1,
  "dangling": 1,
  "no_room": 2,
  "warn_threshold": 0.9,
  "status": "warn"
}
```

`status` is one of `ok`, `warn`, `unknown`. **`rate` is `null` and `status` is `unknown` when `eligible` is 0**: no session that could have joined a call has been recorded, so there is no rate to publish. That is not the same as a rate of 0, and the dashboard renders it as "not measured" rather than "0%".

`ambiguous`, `dangling` and `no_room` account for the uncorrelated remainder: a room name that matched more than one call, a call row that retention has since pruned, and a session that never had a room at all.

Authentication is `require_principal`. A **tenant-scoped key gets 403** rather than the number, because the underlying query has no tenant dimension and the counts are deployment-wide: serving them to one tenant would publish every other tenant's session volume. The self-hosted operator default (no keys configured, or a static config key) is unaffected.

## GET /api/nodes

Per recent call, which infrastructure nodes were sampled during that call's time window. Correlation is **by time window, not by attribution**: VoiceGateway does not claim a given node served a given call, only that these samples fall inside the call's span padded on each side. `window.pad_ms` is that padding and is reported alongside every window so the claim is auditable.

Requires the node scrape to be running (`VOICEGW_NODE_SCRAPE_TARGETS`); with it unset the endpoint returns 200 with `samples_stored: 0` and every window `no_samples`.

**Response (abridged):**

```json
{
  "samples_stored": 322,
  "pad_ms": 15000,
  "calls": [
    {
      "call_id": "8f1c...",
      "correlation": {
        "status": "correlated",
        "window": { "from_ms": 1785000000000, "to_ms": 1785000042000, "pad_ms": 15000 },
        "nodes_sampled": [ { "target": "sfu-1", "ok": 8, "failed": 0 } ]
      }
    }
  ]
}
```

`correlation.status` distinguishes three states that must never be read as each other:

| Status | Meaning |
|---|---|
| `correlated` | Samples exist in the window and are reported with their values. |
| `no_samples` | The scrape ran and there was nothing in this window. **Not a reading**: it does not say the nodes were idle or healthy. `nodes_sampled` is empty. |
| `scrape_failed` | The scrape itself did not succeed, so nothing is known. Values are suppressed, and the failure outcomes are reported instead. |

`correlation` is `null` when the call has no closed span to search, which is distinct again from `no_samples`.

A series the scrape did not return is named and counted rather than dropped or zeroed, and a counter with no sourceable rate reports as not measured rather than `0/s`. Peak statistics carry their own label (`p95`, `max_of_n`, `not_measured`), so a peak over fewer than ten samples is never presented as a p95.

Authentication matches `/api/correlation`, including the 403 for a tenant-scoped key: a node is infrastructure serving every tenant at once, so there is no tenant-scoped answer to give.
## Session reads

The list endpoint plus five reads that hang off a session id back the dashboard's call drill-down:

| Endpoint | Returns |
|---|---|
| `GET /api/sessions` | Recent sessions, newest first. Takes `limit`, `project`, `tenant`, `agent` and `order_by`. |
| `GET /api/sessions/{id}` | One session with its per-modality cost breakdown and provider list. |
| `GET /api/sessions/{id}/turns` | Ordered per-turn rows (latency, response speed). |
| `GET /api/sessions/{id}/transcript` | The captured call transcript, one row per turn. |
| `GET /api/sessions/{id}/dead_air` | Dead-air events for the call, oldest first. |
| `GET /api/sessions/{id}/replay` | The full time-ordered replay: the STT, LLM and TTS payloads of the call. |

**Authentication:** all six require the same read authentication as the other dashboard reads (`/api/costs`, `/api/calls`). As there, the gate is a **no-op while no API keys are configured** (the self-hosted default) and enforces as soon as auth is enabled, when an unauthenticated request gets `401`. Only the list endpoint was gated before, so a session id alone was enough to read the detail, the turns, the transcript, the dead air and the replay of any call on the deployment.

**Tenant scoping:** a tenant-scoped key reads only its own sessions. The per-session routes take no `tenant` parameter (the id is the whole request), so the check runs on the fetched session row. A session belonging to another tenant returns `404` with the same body as a session id that does not exist: a `403` would confirm the id is real. The self-hosted operator (no credential, or a static config key) is an admin principal and keeps reading every session, unchanged.

**Example:**

```bash
curl "http://localhost:8080/api/sessions?limit=20"
curl "http://localhost:8080/api/sessions/vg-8f1c/transcript"
```

The same rows are served by `GET /v1/sessions` and `GET /v1/sessions/{id}` on the [HTTP API](/api/http-api), under the same authentication and the same tenant scoping.

## Replay writes and storage

Three endpoints beside the `GET /api/sessions/{id}/replay` read above:

| Endpoint | Does |
|---|---|
| `DELETE /api/sessions/{id}/replay` | Delete every replay row for the call, cascading across the four `replay_*` tables in one transaction. Returns the row count. |
| `GET /api/replay/storage` | Per-project sum of `sessions.replay_size_bytes`, plus the deployment total. |
| `POST /api/projects/{id}/replay/retention` | Set that project's retention window, `{"retention_days": N}` with N in 1-365. Applied in memory for the retention worker; not persisted to `voicegw.yaml`. |

**Authentication:** the two writes require the **`admin` scope**, the scope every write on a dashboard router takes (API keys, diagnostics runs, the agent probe, the branding logo upload). One destroys captured payloads outright and the other sets how long any of them survive. `GET /api/replay/storage` is a read and takes the same read authentication as the reads above: a read-scoped key is enough. All three gates are **no-ops while no API keys are configured** (the self-hosted default). With auth enabled, an unauthenticated caller gets `401` and a read-scoped key attempting either write gets `403`.

**Tenant scoping:** `GET /api/replay/storage` aggregates every session row, so the tenant resolved from the key becomes a predicate on the query. A tenant-scoped key is told its own footprint only, and the total matches the breakdown it can see. There is no id to `404` on here: what the scoping protects is the size of another tenant's captured traffic and the names of the projects producing it.

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

## API keys

`GET /api/api_keys`, `POST /api/api_keys`, and `POST /api/api_keys/{key_id}/revoke` back the dashboard's API keys screen: list, mint, and soft-revoke the virtual keys (`vk_...`) that authenticate callers.

**Authentication:** every route under `/api/api_keys` requires the `admin` scope, declared on the router so no route can miss it. As with Diagnostics and the Server overview, the gate is a **no-op while no API keys are configured** (the self-hosted default), and it enforces the admin scope as soon as auth is enabled. An unauthenticated request then gets `401`, and a valid token without the `admin` scope gets `403`. The dashboard already sends its bearer token on these calls.

This gate matters because a minted key is issued with the wildcard scope. An ungated mint is a write escalation onto every `/v1` endpoint. A key minted here defaults to `role: tenant`, so **a minted key cannot mint another key** (`403`).

`POST /api/api_keys` takes `name` (required), `tenant_id` (optional), and `issued_by` (optional), and returns `plaintext` exactly once at creation; the dashboard shows the "save this key" modal and discards it. Subsequent list responses expose only `key_prefix`, never the plaintext or the bcrypt hash. Revoke is soft: the row stays for audit with `revoked_at` set, and revoking an already-revoked key returns `404`.

For the equivalent endpoints on the public API, see the [HTTP API](/api/http-api).

## Branding

`GET /api/projects/{id}/branding` returns the project's white-label payload (`logo_url`, `accent_color`, `product_name`), `POST /api/projects/{id}/branding` upserts it, and `POST /api/projects/{id}/branding/logo` uploads a PNG or SVG logo.

**Authentication:** both POSTs require the `admin` scope; the GET does not. White-label branding is an operator/agency setting: the write stamps the `logo_url` every dashboard page renders and the `product_name` it renders as its own name. The **GET stays open to any authenticated reader on purpose**: every dashboard layout mount calls it to apply the brand, and the frontend treats a `403` exactly like a `401` (it clears the stored token and shows the login gate), so gating it behind the admin scope would log out a read-scoped operator on page load. That is why the gate sits on the two write routes and not on the router. Both gates are no-ops while no API keys are configured.

See [Agency quickstart](/guide/agency-quickstart) for what the logo upload accepts.

## Static File Serving

The dashboard also serves the React frontend's built assets. If the frontend has been built (`src/dashboard/frontend/dist/` exists), the daemon serves:

## GET /api/diagnostics/runs/{run_id}

Return one recorded diagnostics run: its status, verdict, gates, and one entry per check that ran. Requires the `admin` scope, like every other diagnostics endpoint, because a run can place billed calls.

Only the `latency` check's per-agent entry is specified here; every check is exported in full by the report endpoints below.

**`latency` check result:**

```json
{
  "ok": true,
  "result": {
    "agents": [
      {
        "agent": "support-voice",
        "stats": { "avg": 0.91, "p50": 0.88, "p95": 1.04, "min": 0.83, "max": 1.04, "trials": 3 },
        "components": { "eou": 0.41, "stt": 0.17, "llm_ttft": 0.4, "tts": 0.21 },
        "error": null
      },
      {
        "agent": "reception",
        "stats": { "avg": 0, "p50": 0, "p95": 0, "min": 0, "max": 0, "trials": 0 },
        "components": null,
        "error": "dispatched to 'reception' but no worker joined within 8s: that name is how the worker registered (register_worker / @server.rtc_session agent_name); check a worker with that name is running"
      }
    ]
  }
}
```

All probe times are **seconds**. `trials` counts the trials that **answered**, so `0` means nothing was measured for that agent and every statistic beside it is `summarize`'s fabricated zero: render it as not measured, never as an instant reply.

`error` is the reason the probe recorded for an agent that answered nothing, verbatim: a dispatch that reached no worker, a connection that failed, a client that raised. `null` means no reason was recorded, which is a different fact from an empty reason (the worker joined and simply never replied). Both surfaces name the failure the same way, because both read this one field: `voicegw livekit latency` and `voicegw livekit check` print `no successful probe (<reason>)` and fall back to `no successful probe (no reply)`, and the dashboard's Latency and Errors tabs say the same. A probe that measured nothing therefore still says why, whenever why is knowable.

The string is written by the LiveKit server or a provider, not by VoiceGateway. Treat it as untrusted remote text: render it as text, never as markup.

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
