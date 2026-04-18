# Observability Tools

These five read-only tools provide visibility into the gateway's health, provider status, costs, latency, and request logs. They never modify state and are safe to call at any time.

## get_health

Return the overall health and identity of the VoiceGateway instance. Use this as a cheap first call to verify the gateway is running before using other tools.

**Destructive:** No

### Input Schema

No parameters required.

```json
{}
```

### Output

| Field | Type | Description |
|---|---|---|
| `version` | `string` | VoiceGateway version (e.g., `"0.1.0"`). |
| `uptime_seconds` | `float` | Seconds since the MCP server started. |
| `status` | `string` | Always `"ok"`. |
| `db_configured` | `boolean` | Whether SQLite storage is enabled. |
| `project_count` | `integer` | Number of configured projects. |
| `provider_count` | `integer` | Number of configured providers. |
| `observability` | `object` | Flags: `latency_tracking`, `cost_tracking`, `request_logging`. |

### Example

**Invocation:**

```json
{
  "name": "get_health",
  "arguments": {}
}
```

**Response:**

```json
{
  "version": "0.1.0",
  "uptime_seconds": 3621.4,
  "status": "ok",
  "db_configured": true,
  "project_count": 3,
  "provider_count": 5,
  "observability": {
    "latency_tracking": true,
    "cost_tracking": true,
    "request_logging": true
  }
}
```

---

## get_provider_status

Return the configuration status of providers. Reports whether each provider has credentials, its type (cloud/local), and model count. Does NOT make live network calls -- use `test_provider` for a connectivity check.

**Destructive:** No

### Input Schema

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `provider_id` | `string \| null` | No | `null` | If set, returns only that provider. Otherwise returns all. |

### Output

| Field | Type | Description |
|---|---|---|
| `providers` | `object` | Map of provider ID to status object. |
| `providers[id].configured` | `boolean` | Whether the provider has credentials or is local. |
| `providers[id].type` | `string` | `"cloud"` or `"local"`. |
| `providers[id].model_count` | `integer` | Number of models registered for this provider. |
| `providers[id].has_api_key` | `boolean` | Whether an API key is set. |

If `provider_id` is specified and not found, `providers` is empty and `missing` contains the requested ID.

### Example: All providers

**Invocation:**

```json
{
  "name": "get_provider_status",
  "arguments": {}
}
```

**Response:**

```json
{
  "providers": {
    "deepgram": {
      "configured": true,
      "type": "cloud",
      "model_count": 2,
      "has_api_key": true
    },
    "whisper": {
      "configured": true,
      "type": "local",
      "model_count": 1,
      "has_api_key": false
    }
  }
}
```

### Example: Single provider

**Invocation:**

```json
{
  "name": "get_provider_status",
  "arguments": { "provider_id": "deepgram" }
}
```

**Response:**

```json
{
  "providers": {
    "deepgram": {
      "configured": true,
      "type": "cloud",
      "model_count": 2,
      "has_api_key": true
    }
  }
}
```

---

## get_costs

Return cost data for a period, optionally filtered by project. Reflects actual invocations recorded in the SQLite request log.

**Destructive:** No

### Input Schema

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `period` | `string` | No | `"today"` | One of: `"today"`, `"week"`, `"month"`, `"all"`. |
| `project` | `string \| null` | No | `null` | Filter to a specific project. |

### Output

| Field | Type | Description |
|---|---|---|
| `period` | `string` | The requested period. |
| `project` | `string \| null` | The project filter, if any. |
| `total_usd` | `float` | Total cost in USD. |
| `by_provider` | `object` | Cost and request count per provider. |
| `by_model` | `object` | Cost and request count per model. |
| `by_project` | `object` | Cost and request count per project (when unfiltered). |

If the database is not enabled, all values are zero.

### Example

**Invocation:**

```json
{
  "name": "get_costs",
  "arguments": { "period": "week", "project": "tonys-pizza" }
}
```

**Response:**

```json
{
  "period": "week",
  "project": "tonys-pizza",
  "total_usd": 12.3456,
  "by_provider": {
    "deepgram": { "cost": 5.1200, "requests": 340 },
    "openai": { "cost": 7.2256, "requests": 120 }
  },
  "by_model": {
    "deepgram/nova-3": { "cost": 5.1200, "requests": 340 },
    "openai/gpt-4o-mini": { "cost": 7.2256, "requests": 120 }
  },
  "by_project": {}
}
```

---

## get_latency_stats

Return latency statistics computed from the request log. Provides overall percentiles (P50, P95, P99) and per-model breakdowns.

**Destructive:** No

### Input Schema

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `period` | `string` | No | `"today"` | One of: `"today"`, `"week"`, `"month"`. |
| `project` | `string \| null` | No | `null` | Filter by project. |
| `modality` | `string \| null` | No | `null` | Filter by `"stt"`, `"llm"`, or `"tts"`. |

### Output

| Field | Type | Description |
|---|---|---|
| `period` | `string` | The requested period. |
| `project` | `string \| null` | The project filter. |
| `modality` | `string \| null` | The modality filter. |
| `overall` | `object` | `p50_ms`, `p95_ms`, `p99_ms`, `avg_ms`, `request_count`. |
| `by_model` | `object` | Per-model stats: `avg_ttfb_ms`, `avg_latency_ms`, `request_count`. |

### Example

**Invocation:**

```json
{
  "name": "get_latency_stats",
  "arguments": { "period": "today", "modality": "stt" }
}
```

**Response:**

```json
{
  "period": "today",
  "project": null,
  "modality": "stt",
  "overall": {
    "p50_ms": 125.0,
    "p95_ms": 280.0,
    "p99_ms": 450.0,
    "avg_ms": 142.5,
    "request_count": 340
  },
  "by_model": {
    "deepgram/nova-3": {
      "avg_ttfb_ms": 120.0,
      "avg_latency_ms": 142.0,
      "request_count": 310
    },
    "whisper/large-v3": {
      "avg_ttfb_ms": 280.0,
      "avg_latency_ms": 890.0,
      "request_count": 30
    }
  }
}
```

---

## get_logs

Return recent request logs with optional filters. Each row is a record from the gateway's SQLite log.

**Destructive:** No

### Input Schema

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `project` | `string \| null` | No | `null` | Filter by project ID. |
| `modality` | `string \| null` | No | `null` | Filter by `"stt"`, `"llm"`, or `"tts"`. |
| `model_id` | `string \| null` | No | `null` | Filter by exact model ID (e.g., `"openai/gpt-4o-mini"`). |
| `status` | `string \| null` | No | `null` | Filter by `"success"`, `"error"`, or `"fallback"`. |
| `limit` | `integer` | No | `50` | Max rows to return (1--1000). |

### Output

A list of log record dicts, each containing:

| Field | Type | Description |
|---|---|---|
| `timestamp` | `float` | Unix timestamp. |
| `project` | `string` | Project ID. |
| `modality` | `string` | `"stt"`, `"llm"`, or `"tts"`. |
| `model_id` | `string` | Full model identifier. |
| `provider` | `string` | Provider name. |
| `cost_usd` | `float` | Cost of this request. |
| `ttfb_ms` | `float` | Time to first byte in milliseconds. |
| `total_latency_ms` | `float` | Total latency in milliseconds. |
| `status` | `string` | `"success"`, `"error"`, or `"fallback"`. |
| `error_message` | `string \| null` | Error details if status is `"error"`. |

### Example

**Invocation:**

```json
{
  "name": "get_logs",
  "arguments": {
    "project": "tonys-pizza",
    "modality": "stt",
    "status": "error",
    "limit": 5
  }
}
```

**Response:**

```json
[
  {
    "timestamp": 1713340981.0,
    "project": "tonys-pizza",
    "modality": "stt",
    "model_id": "deepgram/nova-3",
    "provider": "deepgram",
    "cost_usd": 0.0,
    "ttfb_ms": 0.0,
    "total_latency_ms": 5012.0,
    "status": "error",
    "error_message": "Connection timeout"
  }
]
```
