---
title: HTTP API Reference
description: REST endpoints served by `voicegw serve`. Covers health, status, models, costs, billing, projects, providers, logs, metrics, and audit log.
---

The VoiceGateway HTTP API runs via `voicegw serve` (default port 8080). It provides read-only observability endpoints and full CRUD for managing providers, models, and projects.

Start the server:

```bash
voicegw serve --port 8080
```

## Health

### GET /health

Returns the health status and uptime of the gateway.

**Response:**

```json
{
  "status": "ok",
  "uptime_seconds": 3621.4,
  "version": "0.1.0"
}
```

**Example:**

```bash
curl http://localhost:8080/health
```

---

## Status and Models

### GET /v1/status

Returns the configuration status of all providers and high-level counts.

**Response:**

```json
{
  "providers": {
    "deepgram": { "configured": true, "type": "cloud" },
    "openai": { "configured": true, "type": "cloud" },
    "whisper": { "configured": true, "type": "local" }
  },
  "model_count": 8,
  "project_count": 3
}
```

**Example:**

```bash
curl http://localhost:8080/v1/status
```

### GET /v1/models

List all registered models across all modalities.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project` | `string` | `null` | If set, filters to models used by this project's default stack. |

**Response:**

```json
{
  "models": {
    "deepgram/nova-3": {
      "modality": "stt",
      "provider": "deepgram",
      "model": "nova-3"
    },
    "openai/gpt-4o-mini": {
      "modality": "llm",
      "provider": "openai",
      "model": "gpt-4o-mini"
    }
  },
  "project": null
}
```

**Example:**

```bash
curl http://localhost:8080/v1/models
curl "http://localhost:8080/v1/models?project=tonys-pizza"
```

---

## Costs and Latency

### GET /v1/costs

Return cost summary for a period, optionally filtered by project.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `period` | `string` | `"today"` | One of: `today`, `week`, `month`, `all`. |
| `project` | `string` | `null` | Filter by project ID. |

**Response:**

```json
{
  "period": "today",
  "project": null,
  "total": 1.2345,
  "by_provider": {
    "deepgram": { "cost": 0.5123, "requests": 42 },
    "openai": { "cost": 0.7222, "requests": 18 }
  },
  "by_model": {
    "deepgram/nova-3": { "cost": 0.5123, "requests": 42 }
  },
  "by_project": {
    "tonys-pizza": { "cost": 0.8100, "requests": 35 }
  }
}
```

**Example:**

```bash
curl "http://localhost:8080/v1/costs?period=week"
curl "http://localhost:8080/v1/costs?period=month&project=tonys-pizza"
```

### GET /v1/latency

Return latency statistics for the given period.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `period` | `string` | `"today"` | One of: `today`, `week`, `month`. |
| `project` | `string` | `null` | Filter by project ID. |

**Response:** Per-model latency statistics including average TTFB and total latency.

**Example:**

```bash
curl "http://localhost:8080/v1/latency?period=today"
curl "http://localhost:8080/v1/latency?period=week&project=my-app"
```

---

## Billing

The rating layer's read surface. VoiceGateway rates each recorded request at write time (`rated_price_usd` + `rate_rule`); these endpoints roll that up per tenant and expose the rate card in effect. See [Rating](/architecture/rating) for the model.

### GET /v1/billing/usage

Return rated revenue, recorded cost, and margin per tenant for a window.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `period` | `string` | `"month"` | One of: `today`, `week`, `month`. |
| `start` | `string` | `null` | Start date in `YYYY-MM-DD`. |
| `end` | `string` | `null` | End date in `YYYY-MM-DD` (inclusive day). |
| `project` | `string` | `null` | Filter by project ID. |
| `tenant` | `string` | `null` | Filter to a single tenant. When set, the response also includes `line_items`. |

**Response:**

```json
{
  "period": "month",
  "start": null,
  "end": null,
  "tenant": null,
  "tenants": [
    {
      "tenant_id": "acme",
      "requests": 120,
      "cost_usd": 0.48,
      "rated_usd": 0.72,
      "margin_usd": 0.24,
      "margin_pct": 33.3
    }
  ],
  "totals": {
    "requests": 120,
    "cost_usd": 0.48,
    "rated_usd": 0.72,
    "margin_usd": 0.24
  }
}
```

When `tenant` is passed, the response also carries that tenant's per-(modality, model) line items for invoice detail:

```json
{
  "line_items": [
    {
      "modality": "stt",
      "model_id": "deepgram/nova-3",
      "provider": "deepgram",
      "requests": 120,
      "input_units": 45.0,
      "output_units": 0.0,
      "cost_usd": 0.48,
      "rated_usd": 0.72,
      "margin_usd": 0.24
    }
  ]
}
```

**Example:**

```bash
curl "http://localhost:8080/v1/billing/usage?period=month"
curl "http://localhost:8080/v1/billing/usage?tenant=acme&start=2026-06-01&end=2026-06-30"
```

### GET /v1/billing/rate-card

Return the rate card **in effect**: the global default markup plus every rule, merging the `rate_card:` YAML seed with the DB overrides. The `rule` field on each rule is the audit token stamped onto matching requests (for example `cost_plus:1.3` or `fixed:0.006/minute`).

**Response:**

```json
{
  "default_markup": 1.0,
  "rules": [
    {
      "modality": "stt",
      "provider": "deepgram",
      "model": "nova-3",
      "tenant": null,
      "plan": null,
      "kind": "fixed",
      "markup": null,
      "unit_price_usd": 0.006,
      "unit": "minute",
      "rule": "fixed:0.006/minute"
    }
  ]
}
```

**Example:**

```bash
curl http://localhost:8080/v1/billing/rate-card
```

### GET /v1/billing/rate-card/rules

Return the editable DB override rules, each with its `rule_id`. Use this to drive an editor (the seed rules in `GET /rate-card` have no `rule_id`; only DB overrides are mutable).

### POST /v1/billing/rate-card/rules

Upsert a DB rate-card override for a scope (one rule per scope, keyed by `tenant|plan|modality|provider|model`). Requires the `write` scope. Takes effect on the next config refresh, which the call triggers.

**Body:** a scope (`modality?`, `provider?`, `model?`, `tenant?`, `plan?`) plus either `markup` (cost-plus) or `fixed` + `unit`.

```bash
curl -X POST http://localhost:8080/v1/billing/rate-card/rules \
  -H "Authorization: Bearer $VG_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tenant": "acme", "provider": "deepgram", "markup": 1.1}'
# -> {"rule_id": "acme|*|*|deepgram|*", "created": true}
```

A rule that sets both `markup` and `fixed`, or a fixed rule with a missing/invalid `unit`, returns `400`.

### DELETE /v1/billing/rate-card/rules/{rule_id}

Delete a DB override by its `rule_id` (from `GET /rate-card/rules`). Requires the `write` scope. Returns `404` when no rule has that id.

<Note>
The rate card is one store, edited three ways: the `rate_card:` seed in `voicegw.yaml`, the CLI (`voicegw prices set` / `rm`), and these HTTP endpoints (which the dashboard Rate card page under Configure also uses). See [Rating](/architecture/rating).
</Note>

---

## Projects

### GET /v1/projects

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
curl http://localhost:8080/v1/projects
```

### GET /v1/projects/{project_id}

Return full details for a single project including today's spend and budget status.

**Response:**

```json
{
  "id": "tonys-pizza",
  "name": "Tony's Pizza",
  "description": "Pizza ordering voice agent",
  "daily_budget": 10.0,
  "budget_action": "warn",
  "default_stack": "premium",
  "tags": ["production"],
  "accent": "#e74c3c",
  "today_spend": 2.45,
  "budget_status": "ok",
  "today": { "cost_today": 2.45, "requests_today": 120 },
  "costs_today": { "total": 2.45, "by_provider": {}, "by_model": {} }
}
```

**Example:**

```bash
curl http://localhost:8080/v1/projects/tonys-pizza
```

### POST /v1/projects

Create a new project (stored in SQLite).

**Request body:**

```json
{
  "project_id": "new-app",
  "name": "New App",
  "description": "A new voice agent",
  "daily_budget": 5.0,
  "budget_action": "warn",
  "default_stack": "premium",
  "tags": ["staging"]
}
```

**Response:**

```json
{
  "project_id": "new-app",
  "source": "db",
  "created": true
}
```

**Example:**

```bash
curl -X POST http://localhost:8080/v1/projects \
  -H "Content-Type: application/json" \
  -d '{"project_id":"new-app","name":"New App","daily_budget":5.0}'
```

### PATCH /v1/projects/{project_id}

Update a managed project. Only projects created via the API (source `"db"`) can be updated.

**Request body:** Any subset of fields from the POST body.

**Response:**

```json
{
  "project_id": "new-app",
  "updated": true
}
```

**Example:**

```bash
curl -X PATCH http://localhost:8080/v1/projects/new-app \
  -H "Content-Type: application/json" \
  -d '{"daily_budget":10.0}'
```

### DELETE /v1/projects/{project_id}

Delete a managed project. Requires `?confirm=true` to actually delete. Without the parameter, returns a preview of what would be deleted.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `confirm` | `boolean` | `false` | Must be `true` to perform the deletion. |

**Response (preview):**

```json
{
  "would_delete": { "project_id": "new-app" }
}
```

**Response (confirmed):**

```json
{
  "deleted": "new-app"
}
```

**Example:**

```bash
# Preview
curl -X DELETE "http://localhost:8080/v1/projects/new-app"
# Confirm
curl -X DELETE "http://localhost:8080/v1/projects/new-app?confirm=true"
```

<Warning>
YAML-defined projects cannot be deleted via the API. A `403` is returned.
</Warning>

---

## Providers

### GET /v1/providers

List all providers (YAML-defined and managed).

**Response:**

```json
{
  "providers": [
    {
      "provider_id": "deepgram",
      "source": "yaml",
      "api_key_masked": "sk-a...1f2b",
      "base_url": null
    }
  ]
}
```

**Example:**

```bash
curl http://localhost:8080/v1/providers
```

### POST /v1/providers

Add a new provider (stored in SQLite). The provider type must be one of the supported types.

**Request body:**

```json
{
  "provider_id": "deepgram-staging",
  "provider_type": "deepgram",
  "api_key": "sk-your-api-key",
  "base_url": null
}
```

**Response:**

```json
{
  "provider_id": "deepgram-staging",
  "source": "db",
  "api_key_masked": "sk-y...key"
}
```

**Example:**

```bash
curl -X POST http://localhost:8080/v1/providers \
  -H "Content-Type: application/json" \
  -d '{"provider_id":"deepgram-staging","provider_type":"deepgram","api_key":"sk-your-key"}'
```

### PATCH /v1/providers/{provider_id}

Update a managed provider's API key, base URL, or type.

**Example:**

```bash
curl -X PATCH http://localhost:8080/v1/providers/deepgram-staging \
  -H "Content-Type: application/json" \
  -d '{"api_key":"sk-new-key"}'
```

### DELETE /v1/providers/{provider_id}

Delete a managed provider. Requires `?confirm=true`.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `confirm` | `boolean` | `false` | Must be `true` to perform the deletion. |

**Example:**

```bash
# Preview
curl -X DELETE "http://localhost:8080/v1/providers/deepgram-staging"
# Confirm
curl -X DELETE "http://localhost:8080/v1/providers/deepgram-staging?confirm=true"
```

### POST /v1/providers/{provider_id}/test

Test connectivity to a provider by running its health check.

**Response:**

```json
{
  "status": "ok",
  "latency_ms": 142
}
```

**Example:**

```bash
curl -X POST http://localhost:8080/v1/providers/deepgram/test
```

---

## Models

### POST /v1/models

Register a new model (stored in SQLite).

**Request body:**

```json
{
  "modality": "stt",
  "provider_id": "deepgram",
  "model_name": "nova-3",
  "display_name": "Deepgram Nova 3",
  "default_language": "en"
}
```

**Response:**

```json
{
  "model_id": "deepgram/nova-3",
  "source": "db",
  "created": true
}
```

**Example:**

```bash
curl -X POST http://localhost:8080/v1/models \
  -H "Content-Type: application/json" \
  -d '{"modality":"stt","provider_id":"deepgram","model_name":"nova-3"}'
```

### DELETE /v1/models/{model_id}

Delete a managed model. Requires `?confirm=true`. The `model_id` is a path parameter (for example, `deepgram/nova-3`).

**Example:**

```bash
# Preview
curl -X DELETE "http://localhost:8080/v1/models/deepgram/nova-3"
# Confirm
curl -X DELETE "http://localhost:8080/v1/models/deepgram/nova-3?confirm=true"
```

---

## Logs and Metrics

### GET /v1/logs

Return recent request logs.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | `integer` | `100` | Number of rows (1-1000). |
| `modality` | `string` | `null` | Filter: `stt`, `llm`, or `tts`. |
| `project` | `string` | `null` | Filter by project ID. |

**Response:** An array of log records, each containing `timestamp`, `project`, `modality`, `model_id`, `cost_usd`, `total_latency_ms`, `status`.

**Example:**

```bash
curl "http://localhost:8080/v1/logs?limit=20&modality=stt"
curl "http://localhost:8080/v1/logs?project=tonys-pizza&limit=50"
```

### GET /v1/metrics

Return Prometheus-format metrics (plain text).

**Response (text/plain):**

```
# HELP voicegw_uptime_seconds Process uptime
# TYPE voicegw_uptime_seconds gauge
voicegw_uptime_seconds 3621.4
# HELP voicegw_providers_configured Configured providers
# TYPE voicegw_providers_configured gauge
voicegw_providers_configured 5
# HELP voicegw_cost_usd_total USD cost summed over a ROLLING trailing 24 hours (now-86400s to now). This is a gauge despite the _total suffix: it DECREASES as requests age out of the window. It is not a since-start total and not a calendar day. Do not use rate() or increase() on it.
# TYPE voicegw_cost_usd_total gauge
voicegw_cost_usd_total{period="today"} 1.234500
# TYPE voicegw_requests_total gauge
voicegw_requests_total{provider="deepgram"} 42
voicegw_cost_usd_total{provider="deepgram"} 0.512300
# HELP voicegw_diag_gate_status Health gates in the newest stored LiveKit diagnostics run, counted by gate id and status.
# TYPE voicegw_diag_gate_status gauge
voicegw_diag_gate_status{gate="agents_listing",status="PASS"} 1
voicegw_diag_gate_status{gate="agent_reply_latency",status="UNKNOWN"} 2
# TYPE voicegw_diag_run_verdict gauge
voicegw_diag_run_verdict{verdict="UNKNOWN"} 1
# TYPE voicegw_diag_run_timestamp_seconds gauge
voicegw_diag_run_timestamp_seconds 1785412800.000
```

This endpoint exposes VoiceGateway's own numbers so your Prometheus can scrape it. It is the opposite direction from the node scrape, which pulls exposition text *from* livekit-server and node_exporter *into* this database; nothing scraped from another process is served back out here.

**`voicegw_cost_usd_total` and `voicegw_requests_total` are gauges over a rolling 24-hour window, not counters.** Both are computed from the `"today"` window, which is `now - 86400` seconds: a rolling trailing 24 hours. It is *not* midnight-to-now and *not* a since-process-start total. The value therefore goes **down** as well as up, every time a request falls off the trailing edge. The `period="today"` label and the `_total` suffix are both misnomers kept for backward compatibility, because renaming a scraped series would break every dashboard already built on it. The `# TYPE` metadata is now `gauge`, which is what your tooling actually reads.

Concretely, this means:

```promql
# WRONG. rate()/increase() require a counter. Every decrease (a request ageing
# out of the 24h window) is read as a counter reset, and Prometheus extrapolates
# spend and traffic that never happened.
rate(voicegw_cost_usd_total[5m])
increase(voicegw_requests_total[1h])

# RIGHT. Read the gauge as-is, or aggregate/compare it over time.
voicegw_cost_usd_total{period="today"}                    # spend in the last 24h
sum(voicegw_requests_total)                               # requests in the last 24h
delta(voicegw_cost_usd_total{period="today"}[1h])         # how the 24h window moved
max_over_time(voicegw_cost_usd_total{period="today"}[7d]) # worst 24h in a week
```

`sum(voicegw_requests_total)` sums the per-provider series; there is no separate unlabelled total. Do **not** write bare `sum(voicegw_cost_usd_total)`: that series is emitted three times over, once with `period="today"`, once per `provider` and once per `project`, so an unfiltered `sum` counts the same spend about three times. Always select a label set.

For an actual monotonic spend counter (one that supports `rate()` and `increase()`), VoiceGateway does not publish one yet. Use `GET /v1/costs?period=all`, which is a true since-start total, or `period=week` / `period=month` for wider rolling windows.

The latency series (`voicegw_request_ttfb_seconds`, `voicegw_request_total_latency_seconds`) are summary quantiles over the same rolling trailing 24 hours. Summary quantiles were never counters, so their type is unchanged; no `_sum` or `_count` children are published, so there is nothing to `rate()` there either.

**Diagnostics gate series.** `voicegw_diag_gate_status` reports the health gates of the newest stored [diagnostics run](/cli/livekit) that gated anything, aggregated per gate id and status: the probed agent is not a label, so cardinality does not grow with your fleet. Statuses are the one ladder `voicegw livekit check` uses, `PASS < WARN < UNKNOWN < FAIL`, where **`UNKNOWN` means the gate could not be evaluated and is not a pass** (only `PASS` exits 0). `voicegw_diag_run_verdict` is that run's stored verdict, and `voicegw_diag_run_timestamp_seconds` is when it finished, so a clean verdict from three weeks ago is distinguishable from one from a minute ago.

**Unknown values are omitted, never zero.** No diagnostics run, no readable diagnostics table, or a status this build does not recognise means the series is *absent*. A `0` would be a real observation and would be alerted on; absence is the honest reading. Alert on what is there (for example `voicegw_diag_gate_status{status!="PASS"} > 0`) and on `absent(...)` if you require a run to have happened.

**Example:**

```bash
curl http://localhost:8080/v1/metrics
curl -s http://localhost:8080/v1/metrics | grep voicegw_diag_gate_status
```

---

## Audit Log

### GET /v1/audit-log

Return audit log entries for CRUD operations performed via the API.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | `integer` | `50` | Number of entries (1-500). |
| `entity_type` | `string` | `null` | Filter: `provider`, `model`, or `project`. |
| `entity_id` | `string` | `null` | Filter by specific entity ID. |
| `action` | `string` | `null` | Filter: `create`, `update`, or `delete`. |

**Response:** An array of audit log entries.

**Example:**

```bash
curl "http://localhost:8080/v1/audit-log?entity_type=provider&limit=10"
curl "http://localhost:8080/v1/audit-log?action=delete"
```

## LiveKit Webhooks

### POST /v1/livekit/webhook

Receive LiveKit room and participant lifecycle webhooks and record them as calls. This is what makes a call that runs **no inference** visible: a `calls` row and its `call_legs` are written from webhook events alone, so a call that never reached an LLM still exists in the schema.

Point your LiveKit project's webhook URL at this endpoint. It must be publicly reachable, since LiveKit posts to it.

**Authentication:** the LiveKit webhook signature, not a bearer token. LiveKit cannot send a VoiceGateway API key, so the signature *is* the auth boundary. The request is verified against your LiveKit API key and secret before the body is parsed, and the endpoint **fails closed**: with no LiveKit credentials configured it returns `503` and writes nothing, rather than accepting unsigned writes.

| Response | Meaning |
|---|---|
| `200` | Verified and recorded (or a known-but-unhandled event type, which is ignored). |
| `401` | Missing, malformed, or invalid signature. Nothing was written. |
| `503` | No LiveKit credentials configured, so the signature cannot be verified. |

**Events recorded:** `room_started`, `room_finished`, `participant_joined`, `participant_left`, `participant_connection_aborted`, `track_published`, `track_unpublished`. Egress and ingress events are accepted and ignored.

Delivery is neither ordered nor exactly-once, so every write is an idempotent upsert keyed on `room_sid` (and `participant_sid` for legs). Any event can create the call row, including a `participant_left` that arrives first.

**What this gives you:** `disconnect_reason` from LiveKit includes real layer-1 failure causes, so a `SIP_TRUNK_FAILURE`, `USER_UNAVAILABLE`, or `USER_REJECTED` becomes readable per leg.

**Configuration:** set `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` (or the `livekit:` block in `voicegw.yaml`). Set `VOICEGW_LOADTEST_TRUNK_IDS` to a comma-separated list of SIP trunk ids to mark calls arriving on those trunks as probe traffic, so load tests never pollute production percentiles.

## Call Observations

### POST /v1/calls/observations

Record what only a participating process can see. LiveKit sends no `track_subscribed` webhook, and webhook timestamps are whole seconds, so the agent's own clock is the only source of a millisecond-precision "I published audio at T" for the call: the timestamp that gates the caller's ring time, because `livekit-sip` withholds `200 OK` until it subscribes to an audio track. An agent (or a load worker) posts its own view here; it merges into the same `calls` and `call_legs` rows the webhook receiver writes.

**Authentication:** a VoiceGateway API key with the `write` scope (`Authorization: Bearer vk_...`, or a static key from `auth.api_keys`). Unlike the LiveKit webhook, the caller here is your own agent, which already carries `VOICEGW_API_KEY`. `tenant_id` is taken from the key, never from the body.

**This endpoint does not wait for the database.** The report is validated, queued, and answered; a single background flusher writes it. That is deliberate: the hook runs in the agent's job-start path, so a synchronous write would add latency to the exact number being reported. The queue is bounded at 1000 reports and **drops the newest report when it is full** rather than blocking the agent or growing without limit.

| Response | Meaning |
|---|---|
| `202` | Queued. Not yet written. |
| `429` | The queue is full and this report was **dropped**. Do not retry: retrying makes the overload worse. |
| `401` / `403` | Missing/invalid key, or a key without the `write` scope. |
| `422` | The body carried an unknown field, a timestamp that is not in milliseconds, or no `room_sid`/`attempt_id`. |
| `503` | The path is disabled (`VG_DISABLE_CALL_OBSERVATIONS`), or this collector has no call storage. |

Both the `202` and the `429` body carry the counters, so a reporter can see the loss:

```json
{ "status": "queued", "queue_depth": 3, "dropped_total": 0 }
```

**Example:**

```bash
curl -X POST http://localhost:8080/v1/calls/observations \
  -H "Authorization: Bearer vk_..." \
  -H "Content-Type: application/json" \
  -d '{
    "origin": "agent",
    "room_sid": "RM_abc123",
    "room_name": "call-4821",
    "project": "support",
    "agent_id": "inbound-agent",
    "started_at_ms": 1800000000000,
    "legs": [
      {"participant_sid": "PA_caller", "kind": "SIP", "joined_at_ms": 1800000000100},
      {"participant_sid": "PA_agent", "kind": "AGENT", "joined_at_ms": 1800000001000,
       "first_audio_track_at_ms": 1800000003842, "audio_track_sid": "TR_1",
       "audio_codec": "audio/opus"}
    ]
  }'
```

**Fields:** `origin` (`agent` or `loadgen`, required), one of `room_sid` or `attempt_id` (required: a report with neither cannot be correlated), plus `room_name`, `run_id`, `project`, `agent_id`, `started_at_ms`, `ended_at_ms`, and up to 16 `legs`. A leg carries `participant_sid` (required), `identity`, `kind` (`SIP`/`AGENT`/`STANDARD`/`INGRESS`/`EGRESS`), `joined_at_ms`, `left_at_ms`, `disconnect_reason`, `first_audio_track_at_ms`, `audio_track_sid`, `audio_codec`. All timestamps are **epoch milliseconds**; a seconds- or microseconds-scale value is rejected rather than merged as a nonsense call duration. `calls.channel` and `calls.end_reason` are derived from the reported legs by the same rule the webhook uses (the call's end reason comes from the SIP leg, because that leg is the caller).

**Unknown fields are rejected, not ignored** (`422`). Silently accepting a field nothing stores would let you believe a number was recorded when it was not. So there is no per-call loss, jitter, MOS, or DTMF field (not observable, no column), no SIP response code (`livekit-api` exposes no `ListSIPCallInfo`), no `is_probe` (probe traffic is discriminated by a dedicated load-test trunk, never by anything on the wire), and no `tenant_id`.

**Writes are idempotent.** Reports are merged with the same upserts the webhook uses, keyed on `room_sid`/`attempt_id` (and `participant_sid` for legs), so a re-sent report is a no-op and a report that arrives before any webhook creates the row.

**Configuration:** set `VG_DISABLE_CALL_OBSERVATIONS` to any value (other than `0`/`false`/`no`/`off`) to turn the path off: the endpoint then answers `503`, queues nothing, and starts no flusher. It is read per request, so it takes effect without a restart.
