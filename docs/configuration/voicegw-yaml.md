---
title: voicegw.yaml reference
description: Every top-level section and key in the VoiceGateway config file. Validated with pydantic extra=forbid so typos fail fast at startup.
---

# voicegw.yaml reference

The `voicegw.yaml` file is the central configuration for
VoiceGateway. It is validated at startup using a Pydantic schema
with `extra="forbid"`, which means any typo or unknown key produces
a clear error message before your gateway starts.

VoiceGateway searches for the config file in this order:

1. `./voicegw.yaml` (current directory)
2. `~/.config/voicegateway/voicegw.yaml`
3. `/etc/voicegateway/voicegw.yaml`

You can override this with the `VOICEGW_CONFIG` environment
variable. See [Environment variables](/docs/configuration/environment-variables).

## Top-level sections

The config file has ten top-level sections. All are optional.

| Section | Purpose |
|---|---|
| `providers` | API keys and settings for each provider |
| `models` | Register custom model aliases |
| `stacks` | Named bundles of STT + LLM + TTS models |
| `projects` | Per-project tracking and budgets |
| `fallbacks` | Ordered fallback chains per modality |
| `observability` | Toggle latency, cost, and logging middleware |
| `cost_tracking` | SQLite database settings for cost persistence |
| `latency` | TTFB warning thresholds and percentile config |
| `rate_limits` | Per-provider request rate limits |
| `serve` | Bind host and port for the daemon |

---

## `providers`

Configure credentials and settings for each provider. Keys are
provider names matching VoiceGateway's built-in provider
identifiers.

```yaml
providers:
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}
  openai:
    api_key: ${OPENAI_API_KEY}
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
  groq:
    api_key: ${GROQ_API_KEY}
  cartesia:
    api_key: ${CARTESIA_API_KEY}
  elevenlabs:
    api_key: ${ELEVENLABS_API_KEY}
  assemblyai:
    api_key: ${ASSEMBLYAI_API_KEY}
  ollama:
    base_url: http://localhost:11434
  whisper:
    enabled: true
  kokoro:
    enabled: true
  piper:
    enabled: true
```

Each provider supports at minimum:

- `api_key` (string): API key, typically via `${ENV_VAR}` substitution.
- `base_url` (string): override the default API endpoint.
- `enabled` (bool, default `true`): disable a provider without
  removing its config.

See [Providers](/docs/configuration/providers) for per-provider
details.

---

## `models`

Register custom model aliases organised by modality. Each entry
maps an alias to a `provider` and `model` name, with optional
defaults.

```yaml
models:
  stt:
    fast-transcription:
      provider: deepgram
      model: nova-3
    offline-transcription:
      provider: whisper
      model: large-v3
  llm:
    reasoning:
      provider: anthropic
      model: claude-sonnet-4-5
  tts:
    narrator:
      provider: cartesia
      model: sonic-3
      default_voice: narrator-male
```

See [Models](/docs/configuration/models).

---

## `stacks`

Named bundles that map to one STT, one LLM, and one TTS model. Use
stacks to define preset quality / cost tiers.

```yaml
stacks:
  premium:
    stt: deepgram/nova-3
    llm: anthropic/claude-sonnet-4-5
    tts: cartesia/sonic-3
  budget:
    stt: groq/whisper-large-v3
    llm: groq/llama-3.3-70b-versatile
    tts: local/piper:en_US-lessac-medium
  local:
    stt: local/whisper-large-v3
    llm: ollama/llama3.2:3b
    tts: local/kokoro
```

See [Stacks](/docs/configuration/stacks).

---

## `projects`

Define projects for cost attribution and budget enforcement. Each
project can override providers per-key.

```yaml
projects:
  customer-support:
    name: Customer Support Bot
    description: Production support agent
    default_stack: premium
    daily_budget: 50.00
    budget_action: throttle
    tags: [prod, support]
    providers:
      deepgram:
        api_key: ${SUPPORT_DEEPGRAM_KEY}
      anthropic:
        api_key: ${SUPPORT_ANTHROPIC_KEY}
  internal-qa:
    name: Internal QA Bot
    description: Testing and QA agent
    default_stack: budget
    daily_budget: 10.00
    budget_action: warn
    tags: [dev, qa]

default_project: customer-support
```

`budget_action` is one of `warn`, `throttle`, or `block`. Project-
scoped `providers` override the top-level `providers` for that
project; otherwise the top-level keys apply.

See [Projects](/docs/configuration/projects).

---

## `fallbacks`

Ordered lists of model ids per modality. Used as a resolver-time
hint: walk the list at startup and pick the first model whose
provider plugin imports cleanly.

```yaml
fallbacks:
  stt:
    - deepgram/nova-3
    - openai/whisper-1
    - local/whisper-large-v3
  llm:
    - anthropic/claude-sonnet-4-5
    - openai/gpt-4.1-mini
    - ollama/llama3.2:3b
  tts:
    - cartesia/sonic-3
    - elevenlabs/eleven_multilingual_v2
    - local/kokoro
```

---

## `observability`

Three boolean flags that control which middleware runs. All default
to `true`.

```yaml
observability:
  latency_tracking: true
  cost_tracking: true
  request_logging: true
```

See [Observability](/docs/configuration/observability).

---

## `cost_tracking`

Configure the SQLite storage backend for cost persistence.

```yaml
cost_tracking:
  enabled: true
  db_path: ~/.config/voicegateway/voicegw.db
  daily_budget_alert: 100.00
```

- `enabled` (bool, default `false`): enable cost persistence. Also
  enabled automatically if `VOICEGW_DB_PATH` is set.
- `db_path` (string): path to the SQLite database file.
- `daily_budget_alert` (float, optional): global daily budget alert
  threshold.

---

## `latency`

Configure latency monitoring thresholds.

```yaml
latency:
  ttfb_warning_ms: 500.0
  percentiles: [50.0, 95.0, 99.0]
```

- `ttfb_warning_ms` (float, default `500.0`): time-to-first-byte
  warning threshold in milliseconds.
- `percentiles` (list of floats): which percentiles to track and
  report.

---

## `rate_limits`

Per-provider rate limiting.

```yaml
rate_limits:
  deepgram:
    requests_per_minute: 100
  openai:
    requests_per_minute: 60
```

- `requests_per_minute` (int): maximum requests per minute for the
  given provider.

---

## `serve`

Bind host and port for the daemon. The daemon serves the HTTP API
(`/v1/*`), the dashboard API (`/api/*`), and the React SPA (`/`)
all on this single port.

```yaml
serve:
  host: 0.0.0.0
  port: 8080
```

- `host` (string, default `0.0.0.0`): bind address. Use `127.0.0.1`
  to restrict to localhost.
- `port` (int, default `8080`): port number. The wizard collects
  this as question 4 of [`voicegw onboard`](/docs/cli/onboard).

---

## Environment variable substitution

Any string value in the config can use `${ENV_VAR}` syntax.
VoiceGateway substitutes these at load time using `os.environ`.

```yaml
providers:
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}
```

If the environment variable is not set, it resolves to an empty
string.

See [Environment variables](/docs/configuration/environment-variables).
