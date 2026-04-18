# Model Tools

These three tools manage model registrations on the gateway. Models represent specific AI models from a provider (e.g., `deepgram/nova-3`, `openai/gpt-4o-mini`) that the gateway can route requests to.

## list_models

List every registered model on the gateway, including YAML-defined and GUI-managed models.

**Destructive:** No

### Input Schema

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `modality` | `string \| null` | No | `null` | Filter by `"stt"`, `"llm"`, or `"tts"`. |
| `provider_id` | `string \| null` | No | `null` | Filter by provider (e.g., `"openai"`). |
| `enabled_only` | `boolean` | No | `true` | If `true`, exclude disabled models. |

### Output

| Field | Type | Description |
|---|---|---|
| `models` | `array` | List of model objects. |
| `count` | `integer` | Total matching models. |

Each model object:

| Field | Type | Description |
|---|---|---|
| `model_id` | `string` | Full model identifier (e.g., `"deepgram/nova-3"`). |
| `modality` | `string` | `"stt"`, `"llm"`, or `"tts"`. |
| `provider_id` | `string` | Provider this model belongs to. |
| `model_name` | `string` | Provider-specific model name (e.g., `"nova-3"`). |
| `default_voice` | `string \| null` | Default voice ID (TTS models only). |
| `display_name` | `string \| null` | Human-readable name (managed models only). |
| `default_language` | `string \| null` | Default language code (managed models only). |
| `source` | `string` | `"yaml"` or `"db"`. |
| `enabled` | `boolean` | Whether the model is active. |

### Example: All models

**Invocation:**

```json
{
  "name": "list_models",
  "arguments": {}
}
```

**Response:**

```json
{
  "models": [
    {
      "model_id": "deepgram/nova-3",
      "modality": "stt",
      "provider_id": "deepgram",
      "model_name": "nova-3",
      "default_voice": null,
      "source": "yaml",
      "enabled": true
    },
    {
      "model_id": "openai/gpt-4o-mini",
      "modality": "llm",
      "provider_id": "openai",
      "model_name": "gpt-4o-mini",
      "default_voice": null,
      "source": "yaml",
      "enabled": true
    },
    {
      "model_id": "cartesia/sonic-3",
      "modality": "tts",
      "provider_id": "cartesia",
      "model_name": "sonic-3",
      "default_voice": "sonic-english-female",
      "source": "yaml",
      "enabled": true
    }
  ],
  "count": 3
}
```

### Example: Filter by modality and provider

**Invocation:**

```json
{
  "name": "list_models",
  "arguments": { "modality": "stt", "provider_id": "deepgram" }
}
```

**Response:**

```json
{
  "models": [
    {
      "model_id": "deepgram/nova-3",
      "modality": "stt",
      "provider_id": "deepgram",
      "model_name": "nova-3",
      "default_voice": null,
      "source": "yaml",
      "enabled": true
    }
  ],
  "count": 1
}
```

---

## register_model

Register a new model from an existing provider. The provider must already be configured (either in YAML or added via `add_provider`). The generated model ID is `{provider_id}/{model_name}`.

**Destructive:** No (creates a new resource)

### Input Schema

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `modality` | `string` | Yes | | `"stt"`, `"llm"`, or `"tts"`. |
| `provider_id` | `string` | Yes | | Must match an existing provider. |
| `model_name` | `string` | Yes | | Provider-specific model name (e.g., `"nova-3"`, `"gpt-4o-mini"`). |
| `display_name` | `string \| null` | No | `null` | Human-readable name for dashboards. |
| `default_language` | `string \| null` | No | `null` | Default language code (STT models). |
| `default_voice` | `string \| null` | No | `null` | Default voice ID (TTS models). |
| `config` | `object \| null` | No | `null` | Extra provider-specific options. |

### Output

| Field | Type | Description |
|---|---|---|
| `model_id` | `string` | The generated model ID (e.g., `"deepgram/nova-3"`). |
| `modality` | `string` | The model's modality. |
| `provider_id` | `string` | The provider. |
| `model_name` | `string` | The provider-specific name. |
| `display_name` | `string \| null` | Human-readable name. |
| `default_voice` | `string \| null` | Default voice. |
| `default_language` | `string \| null` | Default language. |
| `source` | `string` | Always `"db"`. |
| `created` | `boolean` | Always `true`. |

### Errors

| Code | When |
|---|---|
| `PROVIDER_NOT_FOUND` | The specified `provider_id` is not configured. |
| `MODEL_ALREADY_EXISTS` | A model with the same ID already exists (YAML or managed). |
| `VALIDATION_ERROR` | Storage is not enabled. |

### Example

**Invocation:**

```json
{
  "name": "register_model",
  "arguments": {
    "modality": "stt",
    "provider_id": "deepgram",
    "model_name": "nova-3",
    "display_name": "Deepgram Nova 3",
    "default_language": "en"
  }
}
```

**Response:**

```json
{
  "model_id": "deepgram/nova-3",
  "modality": "stt",
  "provider_id": "deepgram",
  "model_name": "nova-3",
  "display_name": "Deepgram Nova 3",
  "default_voice": null,
  "default_language": "en",
  "source": "db",
  "created": true
}
```

---

## delete_model

Delete a GUI-registered model. This is a destructive operation that uses the two-phase confirmation pattern. Only models added via `register_model` (source `"db"`) can be deleted; YAML-defined models must be removed from the config file.

**Destructive:** Yes

### Input Schema

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `model_id` | `string` | Yes | | The model ID to delete (e.g., `"openai/gpt-4o-mini"`). |
| `confirm` | `boolean` | No | `false` | Must be `true` to delete. Default returns a preview. |

### Output (preview, confirm=false)

The tool raises a `CONFIRMATION_REQUIRED` error containing:

| Field | Type | Description |
|---|---|---|
| `model_id` | `string` | The model to be deleted. |
| `projects_affected` | `array` | Projects whose stacks reference this model. |

### Output (confirmed, confirm=true)

| Field | Type | Description |
|---|---|---|
| `action` | `string` | `"deleted"`. |
| `model_id` | `string` | The deleted model ID. |
| `projects_affected` | `array` | Projects that were affected. |

### Errors

| Code | When |
|---|---|
| `MODEL_NOT_FOUND` | No managed model with the given ID. |
| `READ_ONLY_RESOURCE` | The model is defined in YAML (cannot delete via MCP). |
| `CONFIRMATION_REQUIRED` | Called without `confirm=true` (returns preview). |

### Example: Preview

**Invocation:**

```json
{
  "name": "delete_model",
  "arguments": { "model_id": "deepgram/nova-3", "confirm": false }
}
```

**Response (error envelope):**

```json
{
  "error": {
    "code": "CONFIRMATION_REQUIRED",
    "message": "Deleting model 'deepgram/nova-3' will impact 1 project(s). Call again with confirm=True.",
    "details": {
      "model_id": "deepgram/nova-3",
      "projects_affected": ["tonys-pizza"]
    }
  }
}
```

### Example: Confirm

**Invocation:**

```json
{
  "name": "delete_model",
  "arguments": { "model_id": "deepgram/nova-3", "confirm": true }
}
```

**Response:**

```json
{
  "action": "deleted",
  "model_id": "deepgram/nova-3",
  "projects_affected": ["tonys-pizza"]
}
```
