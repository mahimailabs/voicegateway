# Python SDK Reference

The `Gateway` class is the main entry point for VoiceGateway. It routes STT, LLM, and TTS requests to configured providers, applying middleware (cost tracking, latency monitoring, rate limiting, budget enforcement, fallback chains) transparently.

## Installation

```bash
pip install voicegateway
# Or with specific provider extras:
pip install "voicegateway[openai,deepgram,cartesia]"
```

## Import

```python
from voicegateway import Gateway, ModelId, GatewayConfig
```

## Gateway

### Constructor

```python
Gateway(config_path: str | None = None)
```

**Arguments:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `config_path` | `str \| None` | `None` | Path to `voicegw.yaml`. If `None`, searches in order: `./voicegw.yaml`, `./gateway.yaml` (legacy), `~/.config/voicegateway/voicegw.yaml`, `/etc/voicegateway/voicegw.yaml`. |

**Example:**

```python
from voicegateway import Gateway

# Auto-discover config
gw = Gateway()

# Explicit path
gw = Gateway(config_path="/etc/voicegateway/voicegw.yaml")
```

### Properties

#### `config`

```python
@property
def config(self) -> GatewayConfig
```

Returns the current gateway configuration object. Read-only.

#### `storage`

```python
@property
def storage(self) -> SQLiteStorage | None
```

Returns the SQLite storage backend if cost tracking is enabled, otherwise `None`.

#### `cost_tracker`

```python
@property
def cost_tracker(self) -> CostTracker
```

Returns the cost tracker middleware instance.

---

## Model Resolution Methods

### `stt()`

```python
def stt(
    model_id: str,
    project: str | None = None,
    **kwargs: Any
) -> Any
```

Create an STT (speech-to-text) provider instance for the given model ID.

**Arguments:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `model_id` | `str` | required | Model identifier in `"provider/model"` or `"provider/model:language"` format. |
| `project` | `str \| None` | `None` | Project ID to tag requests with for cost tracking. Falls back to `"default"`. |
| `**kwargs` | `Any` | | Additional provider-specific options passed to the resolver. |

**Returns:** A provider instance wrapped with instrumentation middleware (cost tracking, latency monitoring).

**Raises:**
- `ValueError` if the model ID cannot be resolved.
- `BudgetExceededError` if the project's daily budget has been exceeded and `budget_action` is `"block"`.

**Example:**

```python
gw = Gateway()

# Basic usage
stt = gw.stt("deepgram/nova-3")

# With project tracking
stt = gw.stt("deepgram/nova-3", project="tonys-pizza")

# With language hint
stt = gw.stt("deepgram/nova-3:es")
```

### `llm()`

```python
def llm(
    model_id: str,
    project: str | None = None,
    **kwargs: Any
) -> Any
```

Create an LLM (large language model) provider instance.

**Arguments:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `model_id` | `str` | required | Model identifier in `"provider/model"` format. |
| `project` | `str \| None` | `None` | Project ID for cost tracking. |
| `**kwargs` | `Any` | | Additional provider-specific options. |

**Returns:** A provider instance wrapped with instrumentation middleware.

**Example:**

```python
llm = gw.llm("openai/gpt-4o-mini", project="my-app")
llm = gw.llm("anthropic/claude-sonnet-4-20250514")
llm = gw.llm("groq/llama-3.3-70b-versatile")
```

### `tts()`

```python
def tts(
    model_id: str,
    project: str | None = None,
    **kwargs: Any
) -> Any
```

Create a TTS (text-to-speech) provider instance.

**Arguments:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `model_id` | `str` | required | Model identifier in `"provider/model"` or `"provider/model:voice_id"` format. |
| `project` | `str \| None` | `None` | Project ID for cost tracking. |
| `**kwargs` | `Any` | | Additional provider-specific options. |

**Returns:** A provider instance wrapped with instrumentation middleware.

**Example:**

```python
tts = gw.tts("cartesia/sonic-3", project="my-app")
tts = gw.tts("elevenlabs/eleven_multilingual_v2:voice_abc123")
tts = gw.tts("kokoro/kokoro-v1")  # local model
```

### `stack()`

```python
def stack(
    name: str,
    project: str | None = None,
    **kwargs: Any
) -> tuple[Any, Any, Any]
```

Resolve a named stack into an `(stt, llm, tts)` tuple. Stacks are defined in `voicegw.yaml` under the `stacks:` section.

**Arguments:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | required | Stack name (e.g., `"premium"`, `"budget"`, `"local"`). |
| `project` | `str \| None` | `None` | Project ID for cost tracking. |
| `**kwargs` | `Any` | | Additional provider-specific options. |

**Returns:** A tuple of `(stt_instance, llm_instance, tts_instance)`. Any component not defined in the stack will be `None`.

**Raises:** `ValueError` if the stack name is not defined in the config.

**Example:**

```python
# voicegw.yaml:
# stacks:
#   premium:
#     stt: deepgram/nova-3
#     llm: openai/gpt-4o-mini
#     tts: cartesia/sonic-3

stt, llm, tts = gw.stack("premium", project="my-app")
```

---

## Fallback Methods

### `stt_with_fallback()`

```python
def stt_with_fallback(
    project: str | None = None,
    **kwargs: Any
) -> Any
```

Create an STT instance using the configured fallback chain. If the primary provider fails, the gateway automatically tries the next provider in the chain.

**Arguments:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project` | `str \| None` | `None` | Project ID for cost tracking. |
| `**kwargs` | `Any` | | Additional provider-specific options. |

**Returns:** A provider instance with automatic fallback behavior.

**Raises:** `ValueError` if no STT fallback chain is configured.

**Example:**

```python
# voicegw.yaml:
# fallbacks:
#   stt: [deepgram/nova-3, assemblyai/universal, whisper/large-v3]

stt = gw.stt_with_fallback(project="production")
```

### `llm_with_fallback()`

```python
def llm_with_fallback(
    project: str | None = None,
    **kwargs: Any
) -> Any
```

Create an LLM instance using the configured fallback chain.

**Arguments:** Same as `stt_with_fallback()`.

**Raises:** `ValueError` if no LLM fallback chain is configured.

### `tts_with_fallback()`

```python
def tts_with_fallback(
    project: str | None = None,
    **kwargs: Any
) -> Any
```

Create a TTS instance using the configured fallback chain.

**Arguments:** Same as `stt_with_fallback()`.

**Raises:** `ValueError` if no TTS fallback chain is configured.

---

## Query Methods

### `status()`

```python
def status(project: str | None = None) -> dict
```

Return the status of all configured providers.

**Arguments:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project` | `str \| None` | `None` | Currently unused (kept for API parity with `costs()`). |

**Returns:** A dict with provider status information including whether each provider is configured and its type (cloud/local).

**Example:**

```python
status = gw.status()
for provider, info in status.items():
    print(f"{provider}: configured={info['configured']}")
```

### `costs()`

```python
def costs(
    period: str = "today",
    project: str | None = None
) -> dict
```

Return cost summary for the given period.

**Arguments:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `period` | `str` | `"today"` | Time period: `"today"`, `"week"`, `"month"`, or `"all"`. |
| `project` | `str \| None` | `None` | Filter by project ID. If `None`, returns costs for all projects. |

**Returns:** A dict with keys `total` (float), `by_provider` (dict), `by_model` (dict). Returns zeros if cost tracking is disabled.

**Example:**

```python
costs = gw.costs("week", project="tonys-pizza")
print(f"Weekly spend: ${costs['total']:.4f}")
for provider, data in costs["by_provider"].items():
    print(f"  {provider}: ${data['cost']:.4f} ({data['requests']} requests)")
```

### `list_projects()`

```python
def list_projects() -> list[dict[str, Any]]
```

Return all configured projects as a list of serializable dicts.

**Returns:** A list of dicts, each containing: `id`, `name`, `description`, `daily_budget`, `default_stack`, `tags`, `accent`.

**Example:**

```python
for project in gw.list_projects():
    print(f"{project['id']}: {project['name']} (budget: ${project['daily_budget']}/day)")
```

### `refresh_config()`

```python
async def refresh_config() -> None
```

Reload the configuration from YAML and SQLite. Called automatically after any managed resource write (provider/model/project creation or deletion). You can call this manually if you edit `voicegw.yaml` while the gateway is running.

**Example:**

```python
import asyncio
asyncio.run(gw.refresh_config())
```

---

## Helper Classes

### `ModelId`

```python
from voicegateway import ModelId

parsed = ModelId.parse("deepgram/nova-3:en")
print(parsed.provider)   # "deepgram"
print(parsed.model)      # "nova-3"
```

Parses `provider/model` and `provider/model:variant` format strings.

### `GatewayConfig`

```python
from voicegateway import GatewayConfig

config = GatewayConfig.load("voicegw.yaml")
print(config.providers)
print(config.models)
print(config.projects)
```

The YAML configuration parser with `${ENV_VAR}` substitution support.
