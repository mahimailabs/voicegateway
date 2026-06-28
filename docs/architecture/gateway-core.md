# Gateway Core

The core layer wires configuration, storage, and middleware together so the `voicegateway.inference` factories and the operations endpoints (CLI, HTTP, MCP, dashboard) all share one source of truth.

## Gateway Class

**File:** `src/voicegateway/core/gateway.py`

`Gateway` is an internal container; it is not part of the public Python SDK. The inference module holds a process-wide singleton via `voicegateway.inference._factory.get_gateway()`. The CLI, HTTP server, and MCP runtime each instantiate it directly because they own their own process lifecycle.

### Initialization

```python
# Internal use only. Not on the public SDK surface.
from voicegateway.core.gateway import Gateway

# Auto-discovers voicegw.yaml from standard locations
gw = Gateway()

# Or specify a config path explicitly
gw = Gateway(config_path="/path/to/voicegw.yaml")
```

Config file search order (when no path is given):

1. `VOICEGW_CONFIG` environment variable.
2. `./voicegw.yaml`
3. `~/.config/voicegateway/voicegw.yaml`
4. `/etc/voicegateway/voicegw.yaml`

### What happens at `Gateway.__init__`

```mermaid
graph TD
    A["Gateway.__init__(config_path)"] --> B["GatewayConfig.load(config_path)"]
    B --> C["Read YAML + substitute ${ENV_VAR}"]
    C --> D["Validate via Pydantic schema"]
    D --> F["SQLiteStorage(db_path)"]
    F --> G["ConfigManager.load_merged()"]
    G --> H["Merge YAML + SQLite managed_* rows"]
    H --> I["Auto-create the 'default' project if missing"]
    I --> J["Init CostTracker, LatencyMonitor, RateLimiter"]
    J --> K["Init BudgetEnforcer, wire into CostTracker"]
```

The database path is resolved as: `VOICEGW_DB_PATH` env > `cost_tracking.db_path` in YAML > default `~/.config/voicegateway/voicegw.db`.

### What `Gateway` exposes

| Surface | Purpose |
|---|---|
| `gw.config` | The merged `GatewayConfig` object (read-only). |
| `gw.storage` | `SQLiteStorage` or `None` when cost tracking is disabled. |
| `gw.cost_tracker` | The `CostTracker` middleware used by the inference wrappers. |
| `gw.costs(period, project=...)` | Cost summary helper used by the CLI and HTTP API. |
| `gw.list_projects()` | Project list for the CLI / dashboard / MCP. |
| `await gw.refresh_config()` | Re-merges YAML and SQLite after a managed_* write. |

The public inference surface is `voicegateway.inference.STT/LLM/TTS`,
which constructs the Gateway singleton internally and reads the same
merged config. There is no separate `Gateway.stt()` / `llm()` /
`tts()` method on the Gateway object.

### Config Refresh

After the dashboard or MCP server writes to managed tables, the Gateway reloads its merged config:

```python
await gw.refresh_config()
```

This re-runs `ConfigManager.load_merged()` and rebuilds the `BudgetEnforcer` so it sees newly-added projects.

## ConfigManager

**File:** `src/voicegateway/core/config_manager.py`

`ConfigManager.load_merged()` deep-copies the YAML config and layers in `managed_providers`, `managed_models`, and `managed_projects` rows from SQLite. Per-project provider rows (those with a non-null `project` column) merge into `merged.projects[<id>].providers[<provider_type>]` so the inference resolver finds them via `GatewayConfig.get_provider_config_for_project`. YAML always wins on conflict.

## inference resolution

**File:** `src/voicegateway/inference/_resolution.py`

The inference factories parse `"provider/model"` strings inline and validate the provider against the registry. The variant suffix (language for STT, voice for TTS) is parsed in the modality-specific factory file (`_stt.py`, `_tts.py`) before resolution; LLM strings keep their trailing colon segments verbatim so Ollama tags survive.

```python
from voicegateway.inference._resolution import resolve_model

resolve_model("deepgram/nova-3")      # ("deepgram", "nova-3")
resolve_model("ollama/qwen2.5:3b")    # ("ollama", "qwen2.5:3b")
```

Errors:

| Exception | When |
|---|---|
| `ModelResolutionError` | Empty string, missing slash, empty halves, or unknown provider name. |

The factories then call `voicegateway.core.registry.create_provider(provider_name, config)` to instantiate the matching `livekit.plugins.<provider>` wrapper.

## Registry

**File:** `src/voicegateway/core/registry.py`

The Registry maps provider names to their implementation classes via lazy import. No provider module is imported until it is actually needed.

```python
_PROVIDER_REGISTRY = {
    "openai":     ("voicegateway.providers.openai_provider", "OpenAIProvider"),
    "deepgram":   ("voicegateway.providers.deepgram_provider", "DeepgramProvider"),
    "cartesia":   ("voicegateway.providers.cartesia_provider", "CartesiaProvider"),
    "anthropic":  ("voicegateway.providers.anthropic_provider", "AnthropicProvider"),
    "groq":       ("voicegateway.providers.groq_provider", "GroqProvider"),
    "elevenlabs": ("voicegateway.providers.elevenlabs_provider", "ElevenLabsProvider"),
    "assemblyai": ("voicegateway.providers.assemblyai_provider", "AssemblyAIProvider"),
    "ollama":     ("voicegateway.providers.ollama_provider", "OllamaProvider"),
    "whisper":    ("voicegateway.providers.whisper_provider", "WhisperProvider"),
    "kokoro":     ("voicegateway.providers.kokoro_provider", "KokoroProvider"),
    "piper":      ("voicegateway.providers.piper_provider", "PiperProvider"),
}
```

`create_provider(name, config)` calls `importlib.import_module()` to load the module, then instantiates the class with the provider config dict. If the import fails (missing SDK), it raises an `ImportError` with an install hint:

```
Could not import provider 'deepgram': No module named 'deepgram'.
Install with: pip install voicegateway[deepgram]
```
