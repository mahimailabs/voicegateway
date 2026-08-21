---
title: voicegw.yaml reference
description: Every top-level section and key in the VoiceGateway config file, validated with pydantic extra=forbid so typos fail fast at startup.
---
`voicegw.yaml` is the central config file for VoiceGateway. It is validated at startup using a Pydantic schema with `extra="forbid"`, so any typo or unknown key produces a clear error before your gateway starts.

## Discovery order

VoiceGateway searches for the file in this order:

1. `./voicegw.yaml` (current directory)
2. `~/.config/voicegateway/voicegw.yaml`
3. `/etc/voicegateway/voicegw.yaml`

Override this entirely by setting `VOICEGW_CONFIG` to an absolute path. See [Environment variables](/configuration/environment-variables).

<Tip>
Run `voicegw init` to write a starter config to `~/.config/voicegateway/voicegw.yaml` with every section commented in.
</Tip>

## Top-level sections

All sections are optional. Omitted sections use defaults.

| Section | Purpose |
|---|---|
| `providers` | API keys and settings for each provider |
| `models` | Register custom model aliases by modality |
| `stacks` | Named STT + LLM + TTS bundles |
| `projects` | Per-project tracking and budgets |
| `fallbacks` | Ordered fallback chains per modality |
| `observability` | Toggle latency, cost, and logging middleware |
| `cost_tracking` | SQLite storage settings |
| `rate_card` | Rating rules that turn recorded cost into a billable price |
| `latency` | TTFB warning thresholds and percentile config |
| `rate_limits` | Per-provider request rate limits |
| `ingest` | Rate limits for the fleet collector ingest endpoint |
| `retention` | Age-out policy for collector data |
| `workers` | Background rollup and retention cadence |
| `serve` | Bind host and port for the daemon, and the provider `base_url` host allowlist |

---

## `providers`

Configure credentials for each provider. String values support `${ENV_VAR}` substitution.

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

All providers support `api_key`, `base_url`, and `enabled` (bool, default `true`). See [Providers](/configuration/providers).

---

## `models`

Register named aliases per modality. Each alias maps to a `provider` and `model`.

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

See [Models](/configuration/models).

---

## `stacks`

Named bundles that map one name to an STT, LLM, and TTS model ID. Reference a stack from a project with `default_stack`.

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

See [Stacks](/configuration/models).

---

## `projects`

Define projects for cost attribution and budget enforcement.

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
    default_stack: budget
    daily_budget: 10.00
    budget_action: warn
    tags: [dev, qa]

default_project: customer-support
```

`budget_action` is one of `warn`, `throttle`, or `block`. Project-level `providers` override the top-level block for that project. See [Projects](/configuration/projects).

### `projects.<id>.metrics`

Conversation-metric tuning, per project rather than global:

```yaml
projects:
  customer-support:
    metrics:
      turn_buffer_flush_size: 25
      talk_over_min_overlap_ms: 100
      dead_air_threshold_seconds: 3.0
```

| Key | Default | Effect |
|---|---|---|
| `turn_buffer_flush_size` | `25` | Turn rows buffered before the tracker writes. See [Turns and transcripts](/guide/turns). |
| `talk_over_min_overlap_ms` | `100` | Minimum caller/agent speech overlap counted as a talk-over. Changes a published rate, not just a threshold: the query previously counted any overlap at all, so figures either side of this are not comparable. |
| `dead_air_threshold_seconds` | `3.0` | Silence, in seconds, before a dead-air event is written. Aggressive for a slow LLM; raise it if thinking time fires events. See [Dead air](/guide/dead-air). |

The dead-air poll interval is a constructor argument, not a config key.

---

## `fallbacks`

Ordered model IDs per modality. The router walks the list at startup and picks the first model whose provider imports cleanly.

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

Three boolean flags that control which middleware runs. All default to `true`.

```yaml
observability:
  latency_tracking: true
  cost_tracking: true
  request_logging: true
```

See [Observability](/guide/attach).

---

## `cost_tracking`

Configure the SQLite storage backend for cost persistence.

```yaml
cost_tracking:
  enabled: true
  db_path: ~/.config/voicegateway/voicegw.db
  daily_budget_alert: 100.00
```

- `enabled` (bool, default `false`): enable cost persistence. Also enabled automatically when `VOICEGW_DB_PATH` is set.
- `db_path` (string): path to the SQLite database file.
- `daily_budget_alert` (float, optional): global daily budget alert threshold in USD.

---

## `rate_card`

The rating layer's price book. VoiceGateway turns each request's recorded provider cost into a billable price and stamps that price immutably onto the request row (`rated_price_usd` + `rate_rule`). The card is a global `default_markup` fallback plus an ordered list of `rules`. For the full model see [Rating](/architecture/rating).

```yaml
rate_card:
  default_markup: 1.30   # optional, default 1.0; global cost-plus fallback
  rules:
    - {provider: openai, markup: 1.5}                                                 # cost_plus: cost x 1.5
    - {modality: stt, provider: deepgram, model: nova-3, fixed: 0.0060, unit: minute} # fixed $/unit
    - {modality: llm, provider: openai, model: gpt-4o, unit: 1m_token,                # fixed, per leg
       input_price_usd: 2.50, cached_input_price_usd: 1.25, output_price_usd: 10.00}
    - {tenant: acme, markup: 1.1}                                                      # per-tenant override
```

- `default_markup` (float, default `1.0`): cost-plus multiplier applied when no rule matches a request.
- `rules` (list): ordered rate rules. Each rule is scoped, names which side of the ledger it sets, and carries exactly one kind of arithmetic.

### Which side a rule sets

`sets` is `price` (default) or `cost`.

- **`price`**: what the tenant is **charged**, computed from the recorded cost. Every rule written before this field existed sets the price, which is why that is the default.
- **`cost`**: what the operator **pays**, replacing the `voice-prices` figure on the recorded row. A cost rule must carry a fixed price: `cost_plus` multiplies a recorded cost, so it cannot produce one.

A cost rule exists because the catalogue holds published list prices, and anyone at volume is on a negotiated contract that differs from them by a margin nobody outside the contract can see. Marking up the catalogue number produces a margin that is wrong in a direction you cannot detect from the recorded row.

```yaml
rate_card:
  rules:
    # what you actually pay Deepgram
    - {sets: cost, modality: stt, provider: deepgram, model: nova-3,
       fixed: 0.0035, unit: minute}
    # what you charge on top of it
    - {sets: price, markup: 1.3}
```

**The two sides resolve independently.** The example above is the ordinary configuration: a model-specific cost plus a global markup. Resolving one merged list most-specific-wins would return the cost rule, apply it as the price, and bill at cost with no margin and nothing in the output saying so. So `cost` and `price` are two separate resolutions over the same list, and both apply.

A cost rule and a price rule can share a scope, since "what I pay for nova-3" and "what I charge for nova-3" are different numbers about the same model. The stored `rule_id` carries the side, so one does not overwrite the other.

When a cost rule applies, the row's `pricing_source` becomes `rate-card:<rule_id>` instead of `voice-prices@<version>`, so a row never claims the catalogue priced it when an operator did.

The collector re-derives cost on ingest. Agents carry no rate card and record the catalogue figure; the collector holds the contract and is the source of truth, so an ingested row is corrected before the markup is applied.

### Scope fields

Every scope field is optional and defaults to "any":

- `modality`: `stt`, `llm`, or `tts`.
- `provider`: a provider name such as `openai` or `deepgram`.
- `model`: a model name, bare (`nova-3`) or fully qualified (`deepgram/nova-3`).
- `tenant`: a tenant ID, for a per-tenant override.
- `plan`: a plan name.

### Rule kind: cost-plus or fixed

A rule is either cost-plus or fixed:

- **cost-plus** (`markup`): billable price is the recorded cost times `markup`. Because it multiplies the recorded cost, it auto-follows voice-prices base movement (change the base price, the rated price tracks it).
- **fixed** (`fixed` + `unit`): billable price is an advertised `$/unit` (the `fixed` value) times the request's billable quantity in `unit`. Decoupled from base cost, so it advertises a stable price as the base moves. Valid units: `minute`, `second` (stt), `char`, `1k_char` (tts), `request` (any modality).
- **fixed, per leg** (`input_price_usd` + `output_price_usd` + a token `unit`): the LLM form. Token units (`token`, `1k_token`, `1m_token`) take a rate per leg rather than a single `fixed`, because every provider prices input and output differently.

#### LLM legs

`cached_input_price_usd` is optional and **defaults to the input rate**, which is what an operator without a negotiated prompt-cache discount pays. Setting it to `0` would make cached tokens free, which is a different claim.

**Cached prompt tokens are a subset of the prompt, not an addition to it.** A 1M-token prompt of which 800k is cached bills 200k at the input rate and 800k at the cached rate, never 1.8M in total.

A unit belongs to one modality (`input_units` holds minutes for stt, characters for tts, prompt tokens for llm), so a rule that names a unit has already said which modality it is for and `modality` may be omitted. Stating a modality that contradicts the unit is rejected when the config loads, as is putting a bare `fixed` on a token unit.

A fixed rule expresses a **flat** contract. It holds one rate per leg, so it cannot reproduce context-window tiered pricing (for example Anthropic's higher rates above a 200k-token prompt). Use `cost_plus` if you need to track a tiered list price.

### Resolution

The single most specific matching rule wins. Precedence is `tenant > plan > global`, and within that `model > provider > modality-only`. A later rule wins a specificity tie, so a rule layered after the seed takes precedence. When no rule matches, the request falls back to the `default_markup` cost-plus pass-through.

### Write-time and immutable

Rating happens once, at write time, on the server (`voicegw serve`). The `rated_price_usd` and `rate_rule` audit token (for example `cost_plus:1.3`, `fixed:0.006/minute`, `fixed:in=2.5,cached=1.25,out=10/1m_token`, or `default:1`) are stamped onto the row and never rewritten: editing the card later never changes historical rows. Inspect and reconcile the card with the [`voicegw prices`](/cli/prices) commands.


---

## `pricing`

```yaml
pricing:
  gate: declared_only   # or: permissive
```

Controls what `serviceable` means on `GET`/`POST /v1/billing/rate-card/quote`, which is the single field a consumer UI gates model enablement on.

- **`declared_only`** (default): only a rate an operator entered counts. A model nobody has priced is not offerable, even when the catalogue carries a published price for it.
- **`permissive`**: any rate counts, including the catalogue's.

The default is strict because a catalogue price is a public list price, wrong by an unknown margin for anyone on a negotiated contract. Treating one as sufficient means nobody ever has to declare theirs, which is the thing the rate card exists to collect. The cost is an empty slate on day one; [`voicegw prices gaps`](/cli/prices) and the catalogue prefill on the quote endpoint exist to make that short.

The quote response echoes `gate` alongside `serviceable`, because `serviceable` is a policy answer rather than a fact, and a consumer written against the other setting would otherwise misread it. `priced_by` (`operator` / `catalog` / `none`) reports where the rate came from under either policy, so a UI can badge list-priced models rather than presenting them as declared.

## `latency`

Configure latency monitoring thresholds.

```yaml
latency:
  ttfb_warning_ms: 500.0
  percentiles: [50.0, 95.0, 99.0]
```

- `ttfb_warning_ms` (float, default `500.0`): time-to-first-byte warning threshold in milliseconds.
- `percentiles` (list of floats): which percentiles to track and report.

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

- `requests_per_minute` (int): maximum requests per minute for the provider.

---

## `ingest`

Rate limiting for the fleet collector ingest endpoint (`POST /v1/ingest`). Limiting uses a per-caller token bucket keyed by virtual key, then static API key, then client IP.

```yaml
ingest:
  enabled: true
  requests_per_minute: 120
  burst: 240
  max_batch_size: 1000
```

- `enabled` (bool, default `true`): turn ingest rate limiting on or off.
- `requests_per_minute` (int, default `120`): sustained per-caller rate. Set to `0` to disable.
- `burst` (int, default `240`): token-bucket ceiling.
- `max_batch_size` (int, default `1000`): maximum records per POST. Larger batches are rejected with `413`.

Over-limit requests receive `429` with a `Retry-After` header. The remote sink honors `Retry-After` and retries without dropping data.

---

## `retention`

Hard-delete aged rows from the collector database. A background worker prunes sessions and their dependent rows by `ended_at`, and requests by `timestamp`, in batches.

```yaml
retention:
  enabled: true
  default_days: 90
```

- `enabled` (bool, default `true`): turn retention pruning on or off.
- `default_days` (int, default `90`): age in days after which rows are deleted.

---

## `workers`

Cadence for background workers: latency and agent rollups, and the retention prune. Workers run in-process and are started by the server. In a multi-replica deployment, set `enabled: false` on every replica except one.

```yaml
workers:
  enabled: true
  rollup_interval_seconds: 900
  retention_interval_seconds: 3600
  node_scrape_interval_seconds: 15
  node_sample_max_age_days: 7
```

- `enabled` (bool, default `true`): start the background workers.
- `rollup_interval_seconds` (int, default `900`): how often the latency and agent rollups refresh.
- `retention_interval_seconds` (int, default `3600`): how often retention runs.
- `node_scrape_interval_seconds` (int, default `15`): how often the node scrape polls, when it runs at all.
- `node_sample_max_age_days` (int, default `7`): how long a raw `node_samples` row is kept. Every scrape tick deletes rows older than this, so the table stays bounded whether or not per-project retention is on.

Raise `node_sample_max_age_days` before running anything you intend to report on
for longer than a week. Retention equal to the observation window prunes the
window's first day before the run ends, and the report then cannot cover the
span the run was performed to demonstrate. It has to exceed the run, not match
it.

The cost is rows: one target at the default 15s interval writes 5,760 rows a
day, so N targets over D days is roughly `N x 5760 x D`. A thirteen-target fleet
held for ten days is a few hundred thousand rows and on the order of a hundred
megabytes.

The node scrape is the one worker that is off by default. It is built only when
`VOICEGW_NODE_SCRAPE_TARGETS` names at least one target, so an install that does
not set that variable starts no scrape task and makes no outbound requests, and
this interval has no effect. See `GET /v1/metrics` in the HTTP API reference for
the target grammar.

---

## `serve`

Bind host and port for the daemon. The daemon serves the HTTP API (`/v1/*`), dashboard API (`/api/*`), and the React SPA (`/`) on this single port.

```yaml
serve:
  host: 0.0.0.0
  port: 8080
  provider_base_url_hosts:
    - proxy.internal.example.com
```

- `host` (string, default `0.0.0.0`): bind address. Use `127.0.0.1` to restrict to localhost.
- `port` (int, default `8080`): port number.
- `provider_base_url_hosts` (list of strings, default empty): hosts a managed provider's `base_url` may be moved to by [`PATCH /v1/providers/{provider_id}`](/api/http-api) when the request does not carry a new `api_key`. Entries are bare hosts (`api.example.com`) or full URLs, whose host is what counts. Values support `${ENV_VAR}` substitution.

### Why `provider_base_url_hosts` exists

A `PATCH` that only changes `base_url` keeps the provider's already-stored API key. `POST /v1/providers/{provider_id}/test` then builds the provider from that row, so the stored key is sent to whatever host the `PATCH` set. Anyone who can reach the write API could point a provider at a host they control and read the key out of the request.

The endpoint therefore constrains the host only for that exact combination: a host change that reuses the stored key. Permitted without any config are the provider's current host and the vendor's own default host (`api.openai.com` for `openai`, `localhost` for `ollama`, and so on), so leaving this list empty changes nothing for an existing deployment. Add a host here to allow a proxy or self-hosted gateway. A `PATCH` that includes its own `api_key` is never constrained: the caller already holds a key, so there is nothing to leak.

---

## Environment variable substitution

Any string value in the config can use `${VAR_NAME}` syntax. VoiceGateway substitutes these at load time from `os.environ`. If the variable is not set, the value resolves to an empty string. See [Environment variables](/configuration/environment-variables).

---

## Explore each section

<CardGroup cols={3}>
  <Card title="Providers" href="/configuration/providers">
    API keys, base URL overrides, and per-project provider blocks for all 11 providers.
  </Card>
  <Card title="Models" href="/configuration/models">
    Model ID format, language and voice suffixes, and custom alias registration.
  </Card>
  <Card title="Stacks" href="/configuration/models">
    Named STT + LLM + TTS bundles for quality tiers.
  </Card>
  <Card title="Projects" href="/configuration/projects">
    Cost attribution, budget enforcement, and per-project provider keys.
  </Card>
  <Card title="Environment variables" href="/configuration/environment-variables">
    All VOICEGW_ and provider API key variables, plus substitution rules.
  </Card>
  <Card title="Observability" href="/guide/attach">
    Latency tracking, cost recording, and request logging middleware.
  </Card>
</CardGroup>
