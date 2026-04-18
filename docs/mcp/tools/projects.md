# Project Tools

These four tools manage projects on the gateway. Projects group requests for cost tracking, budget enforcement, and routing (via default stacks or explicit model assignments).

## list_projects

List every project on the gateway with today's stats, including spend, request count, and budget status.

**Destructive:** No

### Input Schema

No parameters required.

```json
{}
```

### Output

| Field | Type | Description |
|---|---|---|
| `projects` | `array` | List of project objects. |
| `count` | `integer` | Total number of projects. |

Each project object:

| Field | Type | Description |
|---|---|---|
| `id` | `string` | Project identifier. |
| `name` | `string` | Human-readable name. |
| `description` | `string` | Project description. |
| `daily_budget` | `float` | Daily budget in USD (0 = unlimited). |
| `budget_action` | `string` | `"warn"`, `"throttle"`, or `"block"`. |
| `budget_status` | `string` | `"ok"`, `"warning"`, or `"exceeded"`. |
| `today_spend` | `float` | Today's spend in USD. |
| `today_requests` | `integer` | Today's request count. |
| `tags` | `array` | List of tag strings. |
| `default_stack` | `string \| null` | Named stack from config, if set. |
| `source` | `string` | `"yaml"` or `"db"`. |

### Example

**Invocation:**

```json
{
  "name": "list_projects",
  "arguments": {}
}
```

**Response:**

```json
{
  "projects": [
    {
      "id": "tonys-pizza",
      "name": "Tony's Pizza",
      "description": "Pizza ordering voice agent",
      "daily_budget": 10.0,
      "budget_action": "warn",
      "budget_status": "ok",
      "today_spend": 2.45,
      "today_requests": 120,
      "tags": ["production"],
      "default_stack": "premium",
      "source": "yaml"
    },
    {
      "id": "dev-sandbox",
      "name": "Dev Sandbox",
      "description": "Development testing",
      "daily_budget": 1.0,
      "budget_action": "block",
      "budget_status": "warning",
      "today_spend": 0.85,
      "today_requests": 42,
      "tags": ["dev"],
      "default_stack": "local",
      "source": "db"
    }
  ],
  "count": 2
}
```

---

## get_project

Return full details for one project including cost trends and model assignments.

**Destructive:** No

### Input Schema

| Parameter | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | Yes | The ID of the project to fetch. |

### Output

| Field | Type | Description |
|---|---|---|
| `id` | `string` | Project identifier. |
| `name` | `string` | Human-readable name. |
| `description` | `string` | Project description. |
| `daily_budget` | `float` | Daily budget in USD. |
| `budget_action` | `string` | `"warn"`, `"throttle"`, or `"block"`. |
| `budget_status` | `string` | `"ok"`, `"warning"`, or `"exceeded"`. |
| `today_spend` | `float` | Today's spend in USD. |
| `today_requests` | `integer` | Today's request count. |
| `week_spend` | `float` | This week's total spend. |
| `week_requests` | `integer` | This week's total requests. |
| `tags` | `array` | List of tag strings. |
| `default_stack` | `string \| null` | Named stack. |
| `stt_model` | `string \| null` | Explicit STT model (managed projects). |
| `llm_model` | `string \| null` | Explicit LLM model (managed projects). |
| `tts_model` | `string \| null` | Explicit TTS model (managed projects). |
| `source` | `string` | `"yaml"` or `"db"`. |

### Errors

| Code | When |
|---|---|
| `PROJECT_NOT_FOUND` | No project with the given ID exists. |

### Example

**Invocation:**

```json
{
  "name": "get_project",
  "arguments": { "project_id": "tonys-pizza" }
}
```

**Response:**

```json
{
  "id": "tonys-pizza",
  "name": "Tony's Pizza",
  "description": "Pizza ordering voice agent",
  "daily_budget": 10.0,
  "budget_action": "warn",
  "budget_status": "ok",
  "today_spend": 2.45,
  "today_requests": 120,
  "week_spend": 18.90,
  "week_requests": 845,
  "tags": ["production"],
  "default_stack": "premium",
  "stt_model": null,
  "llm_model": null,
  "tts_model": null,
  "source": "yaml"
}
```

---

## create_project

Create a new project for cost tracking and routing. You can either set `default_stack` (referencing a named stack from `voicegw.yaml`) or individual `stt_model`/`llm_model`/`tts_model` fields, but not both.

**Destructive:** No (creates a new resource)

### Input Schema

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `project_id` | `string` | Yes | | Unique identifier (kebab-case recommended). |
| `name` | `string` | Yes | | Human-readable name. |
| `description` | `string` | No | `""` | Long description. |
| `daily_budget` | `float` | No | `0.0` | USD limit per day (0 = unlimited). Must be >= 0. |
| `budget_action` | `string` | No | `"warn"` | `"warn"` logs when exceeded, `"throttle"` falls back to local stack, `"block"` raises an error. |
| `stt_model` | `string \| null` | No | `null` | Explicit STT model ID (must be registered). |
| `llm_model` | `string \| null` | No | `null` | Explicit LLM model ID (must be registered). |
| `tts_model` | `string \| null` | No | `null` | Explicit TTS model ID (must be registered). |
| `default_stack` | `string \| null` | No | `null` | Named stack from `voicegw.yaml` (e.g., `"premium"`). |
| `tags` | `array \| null` | No | `null` | Labels for grouping. |

### Output

| Field | Type | Description |
|---|---|---|
| `project_id` | `string` | The created project ID. |
| `name` | `string` | Project name. |
| `description` | `string` | Project description. |
| `daily_budget` | `float` | Daily budget. |
| `budget_action` | `string` | Budget enforcement action. |
| `default_stack` | `string \| null` | Stack name. |
| `stt_model` | `string \| null` | STT model. |
| `llm_model` | `string \| null` | LLM model. |
| `tts_model` | `string \| null` | TTS model. |
| `tags` | `array` | Tags. |
| `source` | `string` | Always `"db"`. |
| `created` | `boolean` | Always `true`. |
| `created_at` | `float` | Unix timestamp of creation. |

### Errors

| Code | When |
|---|---|
| `PROJECT_ALREADY_EXISTS` | A project with the same ID already exists. |
| `MODEL_NOT_FOUND` | A referenced `stt_model`, `llm_model`, or `tts_model` is not registered. |
| `VALIDATION_ERROR` | Both `default_stack` and explicit models are set, or the stack name does not exist, or storage is disabled. |

### Example: With default stack

**Invocation:**

```json
{
  "name": "create_project",
  "arguments": {
    "project_id": "sushi-bot",
    "name": "Sushi Bot",
    "description": "Sushi restaurant ordering agent",
    "daily_budget": 5.0,
    "budget_action": "warn",
    "default_stack": "premium",
    "tags": ["staging"]
  }
}
```

**Response:**

```json
{
  "project_id": "sushi-bot",
  "name": "Sushi Bot",
  "description": "Sushi restaurant ordering agent",
  "daily_budget": 5.0,
  "budget_action": "warn",
  "default_stack": "premium",
  "stt_model": null,
  "llm_model": null,
  "tts_model": null,
  "tags": ["staging"],
  "source": "db",
  "created": true,
  "created_at": 1713340981.0
}
```

### Example: With explicit models

**Invocation:**

```json
{
  "name": "create_project",
  "arguments": {
    "project_id": "dev-test",
    "name": "Dev Test",
    "daily_budget": 1.0,
    "budget_action": "block",
    "stt_model": "whisper/large-v3",
    "llm_model": "ollama/llama3",
    "tts_model": "kokoro/kokoro-v1"
  }
}
```

---

## delete_project

Delete a managed project. This is a destructive operation that uses the two-phase confirmation pattern. Only projects added via `create_project` (source `"db"`) can be deleted; YAML-defined projects must be removed from the config file. Request logs are NOT deleted -- only the project configuration is removed.

**Destructive:** Yes

### Input Schema

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `project_id` | `string` | Yes | | The ID of the project to delete. |
| `confirm` | `boolean` | No | `false` | Must be `true` to delete. Default returns a preview. |

### Output (preview, confirm=false)

The tool raises a `CONFIRMATION_REQUIRED` error containing:

| Field | Type | Description |
|---|---|---|
| `project_id` | `string` | The project to be deleted. |
| `total_spend_usd` | `float` | All-time spend for this project. |
| `total_requests` | `integer` | All-time request count. |
| `last_activity` | `float \| null` | Unix timestamp of the most recent request. |

### Output (confirmed, confirm=true)

| Field | Type | Description |
|---|---|---|
| `action` | `string` | `"deleted"`. |
| `project_id` | `string` | The deleted project ID. |
| `total_spend_usd` | `float` | All-time spend. |
| `total_requests` | `integer` | All-time request count. |

### Errors

| Code | When |
|---|---|
| `PROJECT_NOT_FOUND` | No project with the given ID. |
| `READ_ONLY_RESOURCE` | The project is defined in YAML, or storage is disabled. |
| `CONFIRMATION_REQUIRED` | Called without `confirm=true` (returns preview). |

### Example: Preview

**Invocation:**

```json
{
  "name": "delete_project",
  "arguments": { "project_id": "dev-test", "confirm": false }
}
```

**Response (error envelope):**

```json
{
  "error": {
    "code": "CONFIRMATION_REQUIRED",
    "message": "Deleting project 'dev-test' is destructive. Review the impact and call again with confirm=True.",
    "details": {
      "project_id": "dev-test",
      "total_spend_usd": 12.34,
      "total_requests": 567,
      "last_activity": 1713340900.0
    }
  }
}
```

### Example: Confirm

**Invocation:**

```json
{
  "name": "delete_project",
  "arguments": { "project_id": "dev-test", "confirm": true }
}
```

**Response:**

```json
{
  "action": "deleted",
  "project_id": "dev-test",
  "total_spend_usd": 12.34,
  "total_requests": 567
}
```
