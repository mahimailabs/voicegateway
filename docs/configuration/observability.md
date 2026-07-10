---
title: Observability
description: VoiceGateway's three built-in observability middleware flags (latency tracking, cost tracking, request logging) and the storage and latency tuning knobs that back them.
---

# Observability

VoiceGateway runs three middleware features around every provider call. All three are enabled by default and can be toggled independently.

## Configuration

```yaml
observability:
  latency_tracking: true
  cost_tracking: true
  request_logging: true
```

---

## `latency_tracking`

**Default:** `true`

When enabled, VoiceGateway measures time-to-first-byte (TTFB) and total latency for every provider call. Latency data is stored in SQLite and available through the dashboard, `voicegw status`, and the HTTP API at `/v1/metrics`.

When disabled, providers are returned without the latency monitoring wrapper. This reduces overhead slightly but removes all latency visibility.

### Latency thresholds

```yaml
latency:
  ttfb_warning_ms: 500.0
  percentiles: [50.0, 95.0, 99.0]
```

- `ttfb_warning_ms`: a warning is logged when TTFB exceeds this threshold (default `500.0` ms).
- `percentiles`: which percentiles to compute and report.

---

## `cost_tracking`

**Default:** `true`

When enabled, VoiceGateway estimates the cost of each provider call based on usage (tokens for LLM, characters for TTS, audio seconds for STT) and records it in SQLite. Cost data powers the dashboard cost views, the `voicegw costs` CLI command, and per-project budget enforcement.

When disabled, no cost records are written and budget enforcement (`budget_action`) will not trigger.

<Warning>
Disabling `cost_tracking` effectively disables budget enforcement for all projects, regardless of their `budget_action` setting.
</Warning>

### Storage settings

```yaml
cost_tracking:
  enabled: true
  db_path: ~/.config/voicegateway/voicegw.db
  daily_budget_alert: 100.00
```

- `enabled`: enable cost persistence (default `false`; also enabled when `VOICEGW_DB_PATH` is set).
- `db_path`: path to the SQLite database file.
- `daily_budget_alert`: global daily budget alert threshold in USD (optional).

### Cost units per modality

| Modality | Billing unit | Example |
|---|---|---|
| STT | Audio seconds | `deepgram/nova-3` billed per second |
| LLM | Input + output tokens | `anthropic/claude-sonnet-4-5` per million tokens |
| TTS | Characters | `cartesia/sonic-3` per character |

Prices are looked up from `voice-prices` (a fork of `pydantic/genai-prices`) at request time. No manual price configuration is needed.

---

## `request_logging`

**Default:** `true`

When enabled, VoiceGateway logs metadata for each provider call: timestamp, provider, model, modality, project, latency, and cost. Logs are stored in SQLite and visible in the dashboard request log view and through the `voicegw logs` CLI command.

When disabled, no request log entries are written and the dashboard log view will be empty.

---

## Disabling all observability

To run VoiceGateway with zero middleware overhead (useful for benchmarking raw provider performance):

```yaml
observability:
  latency_tracking: false
  cost_tracking: false
  request_logging: false
```

---

## Checking current settings

```bash
voicegw status
```

The status output shows which observability features are currently enabled.

---

See [voicegw.yaml reference](/configuration/voicegw-yaml) for the full config file shape.
See [Projects](/configuration/projects) for `budget_action` and per-project budget configuration.
See [Environment variables](/configuration/environment-variables) for `VOICEGW_DB_PATH`.
