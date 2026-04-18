# Dashboard API Reference

The Dashboard API is a separate FastAPI application served by `voicegw dashboard` (default port 9090). It provides read-only endpoints consumed by the React frontend. All endpoints are prefixed with `/api/`.

Start the dashboard:

```bash
voicegw dashboard --port 9090
```

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
    "stt": ["deepgram/nova-3", "whisper/large-v3"],
    "llm": ["openai/gpt-4o-mini", "groq/llama-3.3-70b-versatile"],
    "tts": ["cartesia/sonic-3", "kokoro/kokoro-v1"]
  }
}
```

**Example:**

```bash
curl http://localhost:9090/api/status
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
curl "http://localhost:9090/api/costs?period=week"
curl "http://localhost:9090/api/costs?period=today&project=tonys-pizza"
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
curl "http://localhost:9090/api/latency?period=today"
curl "http://localhost:9090/api/latency?project=my-app"
```

## GET /api/logs

Return recent request logs.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | `integer` | `100` | Number of rows to return (1-1000). |
| `modality` | `string` | `null` | Filter: `stt`, `llm`, or `tts`. |
| `project` | `string` | `null` | Filter by project ID. |

**Response:** An array of log records, each containing `timestamp`, `project`, `modality`, `model_id`, `cost_usd`, `total_latency_ms`, `status`.

**Example:**

```bash
curl "http://localhost:9090/api/logs?limit=50&modality=stt"
curl "http://localhost:9090/api/logs?project=tonys-pizza"
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
curl http://localhost:9090/api/overview
curl "http://localhost:9090/api/overview?project=tonys-pizza"
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
curl http://localhost:9090/api/projects
```

## Static File Serving

The dashboard also serves the React frontend's built assets. If the frontend has been built (`dashboard/frontend/dist/` exists), the dashboard serves:

- `GET /` -- the React app's `index.html`
- `GET /assets/*` -- bundled JavaScript, CSS, and other static files
- All other paths fall through to `index.html` for client-side routing (SPA fallback)

If the frontend has not been built, `GET /` returns an error message with build instructions.
