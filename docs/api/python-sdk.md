# Python SDK Reference

VoiceGateway exposes two public Python surfaces, both supported as of v0.0.5:

- **`voicegateway.inference`** (v0.0.5+) — drop-in mirror of
  `livekit.agents.inference`. Use this for new agents and for
  one-line migrations from LiveKit Cloud Inference. See [the
  `inference` module section below](#voicegatewayinference-module).
- **`voicegateway.Gateway`** (v0.0.1+) — the existing
  configuration-driven factory. Use this when you want explicit
  `gw.stt(...)/llm()/tts()` calls with the project routed
  positionally, fallback chains, and direct access to query helpers
  like `costs()` and `status()`.

Both surfaces produce LiveKit-plugin instances backed by the same
cost-tracking and latency middleware. Picking one over the other is
ergonomic, not architectural — see [Choosing between
`inference` and `Gateway`](#choosing-between-inference-and-gateway).

## Installation

```bash
pip install voicegateway
# Or with specific provider extras:
pip install "voicegateway[openai,deepgram,cartesia]"
```

## Import

```python
# v0.0.5 drop-in surface
from voicegateway import inference

# Configuration-driven surface
from voicegateway import Gateway, ModelId, GatewayConfig
```

## `voicegateway.inference` module

A drop-in mirror of `livekit.agents.inference` (LK 1.5.7). Constructor
signatures match LK's verbatim by name, kind, and default; the
returned object is a LiveKit plugin instance wrapped for cost and
latency tracking, so `AgentSession(stt=..., llm=..., tts=...)` works
unchanged.

### `inference.STT`

```python
inference.STT(
    model: NotGivenOr[STTModels | str] = NOT_GIVEN,
    *,
    language: NotGivenOr[str] = NOT_GIVEN,
    base_url: NotGivenOr[str] = NOT_GIVEN,
    encoding: NotGivenOr[STTEncoding] = NOT_GIVEN,
    sample_rate: NotGivenOr[int] = NOT_GIVEN,
    api_key: NotGivenOr[str] = NOT_GIVEN,
    api_secret: NotGivenOr[str] = NOT_GIVEN,
    http_session: aiohttp.ClientSession | None = None,
    extra_kwargs: NotGivenOr[dict | DeepgramOptions | ...] = NOT_GIVEN,
    fallback: NotGivenOr[list[FallbackModelType] | FallbackModelType] = NOT_GIVEN,
    conn_options: NotGivenOr[APIConnectOptions] = NOT_GIVEN,
)
```

```python
from voicegateway import inference

stt = inference.STT("deepgram/nova-3:en")
# Trailing :en is parsed as the language (mirroring LK STT semantics).
```

The `model` string parses as `provider/model[:language]`. Provider
names are validated against `voicegateway.core.registry` (eleven
supported types). The `api_key` kwarg, when given, overrides the
project's resolved key for this one instance — useful for testing.

### `inference.LLM`

```python
inference.LLM(
    model: LLMModels | str,
    *,
    provider: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
    inference_class: InferenceClass | None = None,
    extra_kwargs: ChatCompletionOptions | dict | None = None,
)
```

```python
llm = inference.LLM("openai/gpt-4o-mini")

# Ollama tags are preserved — LLM does NOT strip the trailing colon
# segment (only STT and TTS do).
llm = inference.LLM("ollama/qwen2.5:3b")

# Explicit provider= overrides any leading "<provider>/" segment in
# the model string. Useful when the model name itself has no slash.
llm = inference.LLM("gpt-4o-mini", provider="openai")
```

LLM uses `None` defaults instead of `NotGivenOr` to match LK's LLM
shape. There is no `fallback`, `conn_options`, or `http_session`
parameter — those are STT/TTS-specific.

### `inference.TTS`

```python
inference.TTS(
    model: TTSModels | str,
    *,
    voice: NotGivenOr[str] = NOT_GIVEN,
    language: NotGivenOr[str] = NOT_GIVEN,
    encoding: NotGivenOr[TTSEncoding] = NOT_GIVEN,
    sample_rate: NotGivenOr[int] = NOT_GIVEN,
    base_url: NotGivenOr[str] = NOT_GIVEN,
    api_key: NotGivenOr[str] = NOT_GIVEN,
    api_secret: NotGivenOr[str] = NOT_GIVEN,
    http_session: aiohttp.ClientSession | None = None,
    extra_kwargs: NotGivenOr[dict | CartesiaOptions | ...] = NOT_GIVEN,
    fallback: NotGivenOr[list[FallbackModelType] | FallbackModelType] = NOT_GIVEN,
    conn_options: NotGivenOr[APIConnectOptions] = NOT_GIVEN,
)
```

```python
tts = inference.TTS("cartesia/sonic-3:my-voice-id")
# Trailing :my-voice-id is parsed as the voice (mirroring LK TTS).

# Or explicit voice kwarg:
tts = inference.TTS("cartesia/sonic-3", voice="my-voice-id")
```

Same shape as STT, plus a `voice` kwarg. The trailing colon-suffix in
the model string parses as voice (NOT language) — that's the
semantic asymmetry between STT and TTS that LiveKit defines.

### `inference.set_project`

```python
inference.set_project(name: str) -> None
```

```python
from voicegateway import inference

inference.set_project("tony-pizza")
stt = inference.STT("deepgram/nova-3")  # uses tony-pizza's key
```

Sets the active project for the current async context. The setting
inherits across awaited coroutines but is isolated across separate
`asyncio.Task` instances. Resolution order for the active project:

1. `inference.set_project(name)` in the current context.
2. `VOICEGW_ACTIVE_PROJECT` environment variable.
3. `default_project` field in `voicegw.yaml`.
4. Hard `ConfigError` if projects are configured but none picked.
   Soft fallback to `"default"` only when no projects exist at all
   (preserves backward compat for pre-v0.0.5 deployments).

### `inference.get_active_project`

```python
inference.get_active_project() -> str
```

Returns the active project name following the resolution order above.
Useful if you need to log or branch on the current scope.

```python
from voicegateway import inference

print(f"Resolving keys for project: {inference.get_active_project()}")
```

### Session correlation

VoiceGateway tags every STT, LLM, and TTS call from the same async
context with one shared `session_id` (`"vg-<uuid4>"`). Inside
`AgentSession` this happens automatically. The id is written to the
`requests.session_id` column and accumulates into the `sessions`
table, so the v0.0.6 dashboard can answer "what did the last call
cost?" without instrumenting your code.

The known gap: factories constructed in separate `asyncio.Task`
instances created **before** the session opens get their own ids.
Construct the factories at session entry, not at module import time.
See the [from-livekit-inference migration
guide](/migration/from-livekit-inference#limitations) for details.

### Choosing between `inference` and `Gateway`

| Use case | API |
|---|---|
| Migrating from `livekit.agents.inference` (one-line swap) | `voicegateway.inference` |
| New agent code in v0.0.5+ | `voicegateway.inference` |
| Existing v0.0.4 agents using `gw.stt/llm/tts` | `Gateway` (no migration required) |
| You want LK-compatible signatures (NotGivenOr defaults, etc.) | `voicegateway.inference` |
| You want explicit project routing as a positional arg | `Gateway` |
| You need YAML-driven `gw.stack("premium")` resolution | `Gateway` |
| You want runtime fallback chains via `gw.stt_with_fallback()` | `Gateway` |
| You want `gw.costs()` / `gw.status()` query helpers | `Gateway` |

Both APIs persist requests through the same middleware pipeline and
storage layer, so cost tracking and reconciliation work identically
on either side. There is **no deprecation** of `Gateway`; the two
surfaces are coexistent and supported.

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
| `model_id` | `str` | required | Model identifier in `"provider/model"` format (e.g., `"deepgram/nova-3"`). |
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
| `model_id` | `str` | required | Model identifier in `"provider/model"` format. For local TTS, use `"local/model:voice"` to select a voice variant. |
| `project` | `str \| None` | `None` | Project ID for cost tracking. |
| `**kwargs` | `Any` | | Additional provider-specific options. |

**Returns:** A provider instance wrapped with instrumentation middleware.

**Example:**

```python
tts = gw.tts("cartesia/sonic-3", project="my-app")
tts = gw.tts("local/kokoro:af_heart")  # local model with voice variant
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
#   stt: [deepgram/nova-3, assemblyai/universal, local/whisper-large-v3]

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
