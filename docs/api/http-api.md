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
# HELP voicegw_cost_usd_total Total cost in USD (today)
# TYPE voicegw_cost_usd_total counter
voicegw_cost_usd_total{period="today"} 1.234500
voicegw_requests_total{provider="deepgram"} 42
voicegw_cost_usd_total{provider="deepgram"} 0.512300
```

**Example:**

```bash
curl http://localhost:8080/v1/metrics
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
