---
title: Provider Tools
description: MCP tools for listing, inspecting, testing, adding, and deleting voice AI providers on the VoiceGateway (list_providers, get_provider, test_provider, add_provider, delete_provider).
---

These five tools manage voice AI providers on the gateway. Use them to list current providers, verify connectivity, add new ones, and remove managed (database) entries.

## list_providers

List every provider configured on the gateway, including both YAML-defined providers and providers added via the API or MCP.

**Destructive:** No

### Input schema

No parameters required.

```json
{}
```

### Output

| Field | Type | Description |
|---|---|---|
| `providers` | array | List of provider objects. |
| `count` | integer | Total number of providers. |

Each provider object:

| Field | Type | Description |
|---|---|---|
| `provider_id` | string | Unique identifier (e.g. `"deepgram"`). |
| `provider_type` | string | The provider implementation type. |
| `source` | string | `"yaml"` (config file) or `"db"` (added via API). |
| `enabled` | boolean | Whether the provider has valid credentials. |
| `api_key_masked` | string or `null` | Masked API key (e.g. `"sk-a...1f2b"`). |
| `base_url` | string or `null` | Custom base URL, if configured. |
| `type` | string | `"cloud"` or `"local"`. |

### Example

```json
{
  "name": "list_providers",
  "arguments": {}
}
```

Response:

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

Return full details for one provider, including the number of models that depend on it. The API key is always masked.

**Destructive:** No

### Input schema

| Parameter | Type | Required | Description |
|---|---|---|---|
| `provider_id` | string | Yes | The ID of the provider to fetch. |

### Output

Same fields as `list_providers` entries, plus:

| Field | Type | Description |
|---|---|---|
| `model_count` | integer | Number of models registered for this provider. |

### Error codes

| Code | When |
|---|---|
| `PROVIDER_NOT_FOUND` | No provider with the given ID exists. |

### Example

```json
{
  "name": "get_provider",
  "arguments": { "provider_id": "deepgram" }
}
```

Response:

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

Test connectivity to a provider by calling its health check method. This makes a real network request to the provider's API to verify credentials and reachability.

**Destructive:** No

### Input schema

| Parameter | Type | Required | Description |
|---|---|---|---|
| `provider_id` | string | Yes | The ID of the provider to test. |

### Output

| Field | Type | Description |
|---|---|---|
| `status` | string | `"ok"` or `"failed"`. |
| `latency_ms` | integer | Round-trip time of the health check in milliseconds. |
| `message` | string | `"reachable"` on success, or an error description on failure. |

### Error codes

| Code | When |
|---|---|
| `PROVIDER_NOT_FOUND` | No provider with the given ID exists. |

### Example

```json
{
  "name": "test_provider",
  "arguments": { "provider_id": "deepgram" }
}
```

Success response:

```json
{
  "status": "ok",
  "latency_ms": 142,
  "message": "reachable"
}
```

Failure response:

```json
{
  "status": "failed",
  "latency_ms": 5012,
  "message": "TimeoutError: Connection timed out"
}
```

---

## add_provider

Register a new voice AI provider. For cloud providers, the gateway validates credentials by running a health check before saving. After adding, use `register_model` to add specific models from this provider.

**Destructive:** No (creates a new resource)

### Input schema

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `provider_id` | string | Yes | | Unique identifier, typically the provider name in lowercase. |
| `provider_type` | string | Yes | | One of: `deepgram`, `openai`, `anthropic`, `groq`, `cartesia`, `elevenlabs`, `assemblyai`, `ollama`, `whisper`, `kokoro`, `piper`. |
| `api_key` | string | No | `""` | API key from the provider console. Leave empty for local providers. |
| `base_url` | string or `null` | No | `null` | Custom base URL (e.g. a self-hosted Ollama instance). |

### Output

| Field | Type | Description |
|---|---|---|
| `provider_id` | string | The created provider ID. |
| `provider_type` | string | The provider type. |
| `api_key_masked` | string or `null` | Masked API key. |
| `base_url` | string or `null` | The base URL, if set. |
| `source` | string | Always `"db"`. |
| `created` | boolean | Always `true`. |

### Error codes

| Code | When |
|---|---|
| `PROVIDER_ALREADY_EXISTS` | A YAML-defined provider has the same ID. |
| `VALIDATION_ERROR` | Unknown `provider_type`, or storage is disabled. |
| `PROVIDER_TEST_FAILED` | The health check failed (credentials invalid or provider unreachable). |

### Example

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

Response:

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

Delete a managed (database-added) provider. This is a destructive operation that uses the two-phase confirmation pattern. YAML-defined providers must be removed from the config file.

**Destructive:** Yes

### Input schema

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `provider_id` | string | Yes | | The ID of the provider to delete. |
| `confirm` | boolean | No | `false` | Must be `true` to delete. Default returns a preview. |

### Output: preview (confirm=false)

The tool raises a `CONFIRMATION_REQUIRED` error containing:

| Field | Type | Description |
|---|---|---|
| `provider_id` | string | The provider to be deleted. |
| `models_affected` | array | Model IDs that reference this provider. |
| `projects_affected` | array | Project IDs that use this provider via stacks. |

### Output: confirmed (confirm=true)

| Field | Type | Description |
|---|---|---|
| `action` | string | `"deleted"`. |
| `provider_id` | string | The deleted provider ID. |
| `models_affected` | array | Models that were affected. |
| `projects_affected` | array | Projects that were affected. |

### Error codes

| Code | When |
|---|---|
| `PROVIDER_NOT_FOUND` | No managed provider with the given ID. |
| `READ_ONLY_RESOURCE` | The provider is defined in YAML (cannot delete via MCP). |
| `CONFIRMATION_REQUIRED` | Called without `confirm=true` (returns preview). |

### Example: preview

```json
{
  "name": "delete_provider",
  "arguments": { "provider_id": "deepgram-staging", "confirm": false }
}
```

Response (error envelope):

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

### Example: confirm

```json
{
  "name": "delete_provider",
  "arguments": { "provider_id": "deepgram-staging", "confirm": true }
}
```

Response:

```json
{
  "action": "deleted",
  "provider_id": "deepgram-staging",
  "models_affected": ["deepgram-staging/nova-3"],
  "projects_affected": []
}
```

<Note>
YAML-defined providers return `READ_ONLY_RESOURCE`. Remove them from `voicegw.yaml` instead. See [Providers configuration](/configuration/providers).
</Note>
