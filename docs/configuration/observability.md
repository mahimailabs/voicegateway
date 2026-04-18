# Observability

VoiceGateway includes three built-in observability features that run as middleware around every provider call. All three are enabled by default and can be toggled independently.

## Configuration

```yaml
observability:
  latency_tracking: true
  cost_tracking: true
  request_logging: true
```

## The three flags

### `latency_tracking`

**Default:** `true`

When enabled, VoiceGateway measures time-to-first-byte (TTFB) and total latency for every provider call. Latency data is stored in SQLite and available through the dashboard, CLI (`voicegw status`), and HTTP API (`/v1/metrics`).

When disabled, provider instances are returned without the latency monitoring wrapper. This reduces overhead slightly but removes all latency visibility.

Related config:

```yaml
latency:
  ttfb_warning_ms: 500.0
  percentiles: [50.0, 95.0, 99.0]
```

- `ttfb_warning_ms` -- a warning is logged when TTFB exceeds this threshold
- `percentiles` -- which percentiles to compute and report

### `cost_tracking`

**Default:** `true`

When enabled, VoiceGateway estimates the cost of each provider call based on usage (tokens, characters, audio seconds) and records it in the SQLite database. Cost data powers the dashboard cost views, the `voicegw costs` CLI command, and per-project budget enforcement.

When disabled, no cost records are written. Budget enforcement (`budget_action`) will not trigger because there is no spend data to compare against.

::: warning
Disabling `cost_tracking` also effectively disables budget enforcement for all projects, regardless of their `budget_action` setting.
:::

Related config:

```yaml
cost_tracking:
  enabled: true
  db_path: ~/.config/voicegateway/voicegw.db
  daily_budget_alert: 100.00
```

### `request_logging`

**Default:** `true`

When enabled, VoiceGateway logs metadata about each provider call: timestamp, provider, model, modality, project, latency, and cost. Logs are stored in SQLite and visible in the dashboard request log view and through the `voicegw logs` CLI command.

When disabled, no request log entries are written. The dashboard log view will be empty.

## Disabling all observability

To run VoiceGateway with zero overhead from observability middleware:

```yaml
observability:
  latency_tracking: false
  cost_tracking: false
  request_logging: false
```

This is useful for benchmarking raw provider performance or in environments where you handle monitoring externally.

## Checking current settings

```bash
voicegw status
```

The status output includes which observability features are enabled.

See: [voicegw.yaml Reference](/configuration/voicegw-yaml), [Projects](/configuration/projects), [Environment Variables](/configuration/environment-variables)
