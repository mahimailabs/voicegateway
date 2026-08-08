---
title: Configuration layers
description: How VoiceGateway merges voicegw.yaml, SQLite managed tables, and environment variables into a single resolved GatewayConfig, with clear priority rules and a live refresh cycle.
---
VoiceGateway merges configuration from three sources with a clear priority order. You can pin critical settings in YAML, manage everything else through the dashboard or MCP server, and override individual values at runtime with environment variables.

## Priority order

```
ENV variables (highest)  >  SQLite managed tables  >  YAML file (lowest)
```

Environment variables act by substituting into `${VAR}` placeholders inside `voicegw.yaml` before it is parsed (below), plus a handful of settings read from a specific env var directly and bypassing the file entirely (`VOICEGW_CONFIG` picks which YAML file to load; `VOICEGW_DB_PATH`/`VOICEGW_DB_URL` can override `cost_tracking.db_path`). The DB-vs-YAML merge itself, done by `ConfigManager`, never looks at environment variables again.

```mermaid
graph TB
    subgraph Sources["Configuration sources"]
        ENV["Environment variables<br/>${VAR} substitution, plus a few<br/>direct overrides (VOICEGW_DB_PATH, ...)"]
        YAML["voicegw.yaml<br/>(env vars already substituted)"]
        DB["SQLite managed tables<br/>managed_projects, managed_providers, managed_models"]
    end

    subgraph Merge["ConfigManager.load_merged()"]
        M1["1. Deep-copy the YAML-derived GatewayConfig"]
        M2["2. Layer in managed_projects"]
        M3["3. Layer in managed_providers<br/>(a row with project set nests into<br/>that project's providers dict, not top-level)"]
        M4["4. Layer in managed_models"]
    end

    subgraph Result["Merged GatewayConfig"]
        R["providers + models + projects + rate_card + ..."]
    end

    ENV --> YAML
    YAML --> M1
    M1 --> M2
    DB --> M2
    M2 --> M3
    M3 --> M4
    M4 --> R
```

## ConfigManager

**File:** `src/voicegateway/core/config_manager.py`

`ConfigManager` merges the YAML config and SQLite managed rows into a single `GatewayConfig`.

```python
class ConfigManager:
    def __init__(self, yaml_config: GatewayConfig, storage: StorageService | None):
        self._yaml = yaml_config
        self._storage = storage

    async def load_merged(self) -> GatewayConfig:
        """Return a GatewayConfig with managed_* rows merged in."""
        merged = copy.deepcopy(self._yaml)
        # Layer in managed projects, then providers, then models, from SQLite.
        ...
        return merged

    async def refresh(self) -> GatewayConfig:
        """Reload managed resources from SQLite. Used after a write."""
        return await self.load_merged()
```

### Merge rules

YAML always takes precedence. If a provider, model, or project exists in both YAML and SQLite (matched by id), the YAML version wins:

```python
for row in await self._storage.list_managed_providers():
    pid = row["provider_id"]
    ...
    if project_name:
        # A managed_providers row scoped to a project nests into that
        # project's own providers dict, not the top-level one.
        if project_name not in merged.projects:
            merged.projects[project_name] = ProjectConfig(id=project_name, name=project_name, source="db")
        project = merged.projects[project_name]
        if provider_type in project.providers:
            continue  # YAML per-project entry wins.
        project.providers[provider_type] = provider_cfg
    else:
        if pid in merged.providers:
            continue  # YAML top-level provider wins.
        merged.providers[pid] = provider_cfg
```

This lets you pin critical configuration in `voicegw.yaml` and use the dashboard or MCP for everything else, without risk of managed resources overwriting file-based config. The real merge order is **projects, then providers, then models**: providers merge after projects because a per-project provider row needs somewhere to land, and a managed project row arriving after a managed provider row that targets it would otherwise have nothing to nest into.

### The `source` field

Each `ProjectConfig` carries a `source` field indicating origin:

| `source` value | Meaning |
|---|---|
| `"yaml"` | Defined in `voicegw.yaml` |
| `"db"` | Created via the dashboard or MCP, stored in `managed_projects` (or auto-created to hold a per-project `managed_providers` row that named a project not otherwise defined) |

For providers and models, the `_source` key is injected into the config dict (`provider_cfg` above): it starts from the row's `extra_config` JSON, then `api_key`, `base_url`, and `_source: "db"` are set on top of it, so nothing in `extra_config` can shadow the decrypted key.

## YAML configuration

**File:** `src/voicegateway/core/config.py`

### Environment variable substitution

YAML values containing `${ENV_VAR}` are replaced with the corresponding environment variable at load time:

```yaml
providers:
  openai:
    api_key: ${OPENAI_API_KEY}
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}
```

Substitution is recursive: it works inside strings, dicts, and lists. Missing env vars resolve to empty strings.

### Config file search

When no explicit path is provided, VoiceGateway searches in this order:

1. `VOICEGW_CONFIG` environment variable.
2. `./voicegw.yaml` in the current directory.
3. `~/.config/voicegateway/voicegw.yaml`.
4. `/etc/voicegateway/voicegw.yaml`.

### Pydantic validation

**File:** `src/voicegateway/schemas/config_schema.py`

The raw YAML dict is validated against `VoiceGatewayConfig` before use. Validation errors include field paths and messages:

```
Configuration validation failed:
  - providers.openai.api_key: field required
  - cost_tracking.db_path: str type expected

Check your voicegw.yaml for typos or invalid values.
```

### GatewayConfig dataclass

The parsed config is stored as a `GatewayConfig` dataclass (`src/voicegateway/core/config.py`):

| Field | Type | Description |
|---|---|---|
| `providers` | `dict[str, dict]` | Provider configs keyed by name |
| `models` | `dict[str, dict[str, dict]]` | Models keyed by modality, then model ID |
| `fallbacks` | `dict[str, list[str]]` | Fallback chains per modality |
| `cost_tracking` | `dict` | DB path, enabled flag |
| `latency` | `dict` | TTFB warning threshold |
| `rate_limits` | `dict[str, dict]` | RPM limits per provider |
| `dashboard` | `dict` | Dashboard config |
| `serve` | `dict` | `voicegw serve` runtime knobs |
| `projects` | `dict[str, ProjectConfig]` | Project configs |
| `default_project` | `str \| None` | Project a call lands under when none is set explicitly |
| `stacks` | `dict[str, dict[str, str]]` | Named model bundles |
| `auth` | `AuthConfig` | HTTP API keys + CORS origins |
| `observability` | `dict` | Feature flags for tracking |
| `ingest` | `IngestConfig` | Rate limits for `POST /v1/ingest` on a collector |
| `retention` | `RetentionConfig` | Collector-wide default retention window |
| `workers` | `WorkersConfig` | Background-worker cadence (rollups, retention, node scrape) |
| `clickhouse` | `ClickHouseConfig` | ClickHouse sink connection, for a collector |
| `rate_card` | `dict` | `default_markup` + rules; built into a `RateCard` at wiring. See [Rating](/architecture/rating) |

## Refresh cycle

When the dashboard or MCP server creates, updates, or deletes a managed resource, the config is refreshed without a server restart:

```mermaid
sequenceDiagram
    participant API as Dashboard / MCP / HTTP API
    participant DB as SQLite
    participant GW as Gateway
    participant CM as ConfigManager

    API->>DB: upsert_managed_provider(...)
    API->>DB: log_audit_event(...)
    API->>GW: refresh_config()
    GW->>CM: refresh()
    CM->>DB: list_managed_projects()
    CM->>DB: list_managed_providers()
    CM->>DB: list_managed_models()
    CM-->>GW: merged GatewayConfig
    GW->>GW: rebuild BudgetEnforcer against the new config
    GW->>GW: rebuild the effective RateCard (rate_card seed + DB rate-rule overrides)
```

<Note>
Newly added providers and models are immediately available after the refresh. No restart needed. There is no separate router object to rebuild; the `core/router.py`/`core/model_id.py` this page used to describe are gone, and the closest live code, `core/model_resolution.py`, only parses `"provider/model"` strings against the provider registry.
</Note>

## Example configuration

```yaml
providers:
  openai:
    api_key: ${OPENAI_API_KEY}
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}
  cartesia:
    api_key: ${CARTESIA_API_KEY}

models:
  stt:
    deepgram/nova-3:
      provider: deepgram
      model: nova-3
  llm:
    openai/gpt-4.1-mini:
      provider: openai
      model: gpt-4.1-mini
  tts:
    cartesia/sonic-3:
      provider: cartesia
      model: sonic-3
      default_voice: 794f9389-aac1-45b6-b726-9d9369183238

fallbacks:
  stt:
    - deepgram/nova-3
    - openai/whisper-1
  tts:
    - cartesia/sonic-3
    - elevenlabs/turbo-v2.5

stacks:
  premium:
    stt: deepgram/nova-3
    llm: openai/gpt-4.1-mini
    tts: cartesia/sonic-3

projects:
  prod:
    name: Production
    daily_budget: 50.00
    budget_action: throttle
    default_stack: premium
    tags: [production]

cost_tracking:
  enabled: true
  db_path: ~/.config/voicegateway/voicegw.db

rate_limits:
  openai:
    requests_per_minute: 60
  deepgram:
    requests_per_minute: 100

latency:
  ttfb_warning_ms: 500

observability:
  latency_tracking: true
  cost_tracking: true
  request_logging: true
```

## Related pages

<CardGroup cols={2}>
  <Card title="voicegw.yaml reference" href="/configuration/voicegw-yaml">
    Full YAML schema with all fields and defaults.
  </Card>
  <Card title="Environment variables" href="/configuration/environment-variables">
    All supported environment variables and their defaults.
  </Card>
  <Card title="Storage" href="/architecture/storage">
    The SQLite tables that back managed configuration.
  </Card>
  <Card title="Security" href="/architecture/security">
    How managed provider API keys are encrypted at rest.
  </Card>
</CardGroup>
