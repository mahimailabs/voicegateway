# Provider Tools

These five tools manage voice AI providers on the gateway. They allow agents to list, inspect, test, add, and delete providers.

## list_providers

List every provider configured on the gateway, including both YAML-defined providers and providers added via the API or MCP.

**Destructive:** No

### Input Schema

No parameters required.

```json
{}
```

### Output

| Field | Type | Description |
|---|---|---|
| `providers` | `array` | List of provider objects. |
| `count` | `integer` | Total number of providers. |

Each provider object:

| Field | Type | Description |
|---|---|---|
| `provider_id` | `string` | Unique identifier (e.g., `"deepgram"`). |
| `provider_type` | `string` | The provider implementation type. |
| `source` | `string` | `"yaml"` (config file) or `"db"` (added via API). |
| `enabled` | `boolean` | Whether the provider has valid credentials. |
| `api_key_masked` | `string \| null` | Masked API key (e.g., `"sk-a...1f2b"`). |
| `base_url` | `string \| null` | Custom base URL, if configured. |
| `type` | `string` | `"cloud"` or `"local"`. |

### Example

**Invocation:**

```json
{
  "name": "list_providers",
  "arguments": {}
}
```

**Response:**

```json
{
  "providers": [
    {
      "provider_id": "deepgram",
      "provider_type": "deepgram",
      "source": "yaml",
      "enabled": true,
      "api_key_masked": "sk-a...1f2b",
      "base_url": null,
      "type": "cloud"
    },
    {
      "provider_id": "whisper",
      "provider_type": "whisper",
      "source": "yaml",
      "enabled": true,
      "api_key_masked": null,
      "base_url": null,
      "type": "local"
    }
  ],
  "count": 2
}
```

---

## get_provider

Return full details for one provider, including how many models depend on it. The API key is always masked.

**Destructive:** No

### Input Schema

| Parameter | Type | Required | Description |
|---|---|---|---|
| `provider_id` | `string` | Yes | The ID of the provider to fetch. |

### Output

Same fields as `list_providers` entries, plus:

| Field | Type | Description |
|---|---|---|
| `model_count` | `integer` | Number of models registered for this provider. |

### Errors

| Code | When |
|---|---|
| `PROVIDER_NOT_FOUND` | No provider with the given ID exists. |

### Example

**Invocation:**

```json
{
  "name": "get_provider",
  "arguments": { "provider_id": "deepgram" }
}
```

**Response:**

```json
{
  "provider_id": "deepgram",
  "provider_type": "deepgram",
  "source": "yaml",
  "enabled": true,
  "api_key_masked": "sk-a...1f2b",
  "base_url": null,
  "type": "cloud",
  "model_count": 2
}
```

---

## test_provider

Test connectivity to a provider by calling its `health_check()` method. This makes a real network request to the provider's API to verify credentials and reachability.

**Destructive:** No

### Input Schema

| Parameter | Type | Required | Description |
|---|---|---|---|
| `provider_id` | `string` | Yes | The ID of the provider to test. |

### Output

| Field | Type | Description |
|---|---|---|
| `status` | `string` | `"ok"` or `"failed"`. |
| `latency_ms` | `integer` | Round-trip time of the health check in milliseconds. |
| `message` | `string` | `"reachable"` on success, or an error description. |

### Errors

| Code | When |
|---|---|
| `PROVIDER_NOT_FOUND` | No provider with the given ID exists. |

### Example

**Invocation:**

```json
{
  "name": "test_provider",
  "arguments": { "provider_id": "deepgram" }
}
```

**Response (success):**

```json
{
  "status": "ok",
  "latency_ms": 142,
  "message": "reachable"
}
```

**Response (failure):**

```json
{
  "status": "failed",
  "latency_ms": 5012,
  "message": "TimeoutError: Connection timed out"
}
```

---

## add_provider

Register a new voice AI provider. The gateway validates the credentials by running a health check before saving (for cloud providers). After adding, use `register_model` to add specific models from this provider.

**Destructive:** No (creates a new resource)

### Input Schema

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `provider_id` | `string` | Yes | | Unique identifier, typically the provider name in lowercase. |
| `provider_type` | `string` | Yes | | One of: `deepgram`, `openai`, `anthropic`, `groq`, `cartesia`, `elevenlabs`, `assemblyai`, `ollama`, `whisper`, `kokoro`, `piper`. |
| `api_key` | `string` | No | `""` | API key from the provider console. Empty for local providers. |
| `base_url` | `string \| null` | No | `null` | Custom base URL (e.g., self-hosted Ollama). |

### Output

| Field | Type | Description |
|---|---|---|
| `provider_id` | `string` | The created provider ID. |
| `provider_type` | `string` | The provider type. |
| `api_key_masked` | `string \| null` | Masked API key. |
| `base_url` | `string \| null` | The base URL, if set. |
| `source` | `string` | Always `"db"`. |
| `created` | `boolean` | Always `true`. |

### Errors

| Code | When |
|---|---|
| `PROVIDER_ALREADY_EXISTS` | A YAML-defined provider has the same ID. |
| `VALIDATION_ERROR` | Unknown `provider_type`, or storage is disabled. |
| `PROVIDER_TEST_FAILED` | The health check failed (credentials invalid or unreachable). |

### Example

**Invocation:**

```json
{
  "name": "add_provider",
  "arguments": {
    "provider_id": "deepgram-staging",
    "provider_type": "deepgram",
    "api_key": "sk-your-staging-key"
  }
}
```

**Response:**

```json
{
  "provider_id": "deepgram-staging",
  "provider_type": "deepgram",
  "api_key_masked": "sk-y...key",
  "base_url": null,
  "source": "db",
  "created": true
}
```

---

## delete_provider

Delete a managed (GUI/API-added) provider. This is a destructive operation that uses the two-phase confirmation pattern.

**Destructive:** Yes

### Input Schema

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `provider_id` | `string` | Yes | | The ID of the provider to delete. |
| `confirm` | `boolean` | No | `false` | Must be `true` to actually delete. Default returns a preview. |

### Output (preview, confirm=false)

The tool raises a `CONFIRMATION_REQUIRED` error containing:

| Field | Type | Description |
|---|---|---|
| `provider_id` | `string` | The provider to be deleted. |
| `models_affected` | `array` | List of model IDs that reference this provider. |
| `projects_affected` | `array` | List of project IDs that use this provider via stacks. |

### Output (confirmed, confirm=true)

| Field | Type | Description |
|---|---|---|
| `action` | `string` | `"deleted"`. |
| `provider_id` | `string` | The deleted provider ID. |
| `models_affected` | `array` | Models that were affected. |
| `projects_affected` | `array` | Projects that were affected. |

### Errors

| Code | When |
|---|---|
| `PROVIDER_NOT_FOUND` | No managed provider with the given ID. |
| `READ_ONLY_RESOURCE` | The provider is defined in YAML (cannot delete). |
| `CONFIRMATION_REQUIRED` | Called without `confirm=true` (returns preview). |

### Example: Preview

**Invocation:**

```json
{
  "name": "delete_provider",
  "arguments": { "provider_id": "deepgram-staging", "confirm": false }
}
```

**Response (error envelope):**

```json
{
  "error": {
    "code": "CONFIRMATION_REQUIRED",
    "message": "Deleting provider 'deepgram-staging' will impact 1 model(s) and 0 project(s). Call again with confirm=True to proceed.",
    "details": {
      "provider_id": "deepgram-staging",
      "models_affected": ["deepgram-staging/nova-3"],
      "projects_affected": []
    }
  }
}
```

### Example: Confirm

**Invocation:**

```json
{
  "name": "delete_provider",
  "arguments": { "provider_id": "deepgram-staging", "confirm": true }
}
```

**Response:**

```json
{
  "action": "deleted",
  "provider_id": "deepgram-staging",
  "models_affected": ["deepgram-staging/nova-3"],
  "projects_affected": []
}
```
