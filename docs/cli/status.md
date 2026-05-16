---
title: voicegw status
description: Show provider configuration status. Useful for verifying setup after editing voicegw.yaml or adding providers via the API.
---

# voicegw status

Show the configuration status of all providers.

## Purpose

`voicegw status` displays a table of every provider defined in the
config, whether it has credentials configured, and how many models
are registered against it. This is the quickest way to verify your
setup after editing `voicegw.yaml` or adding providers via the API.

## Syntax

```bash
voicegw status [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | `string` | auto | Path to `voicegw.yaml`. Auto-discovered if omitted. |
| `--project` | `-p` | `string` | `null` | Filter to one project (validates the project id exists). |

## Output

A Rich-formatted table with three columns:

| Column | Description |
|---|---|
| **Provider** | Provider name (e.g., `deepgram`, `openai`, `whisper`). |
| **Configured** | `Yes` when an API key is set or the provider is local; `No API key` otherwise. |
| **Models** | Number of models registered for this provider across all modalities. |

## Examples

### Show all provider status

```bash
voicegw status
```

```
         Provider Status
┌───────────┬────────────┬────────┐
│ Provider  │ Configured │ Models │
├───────────┼────────────┼────────┤
│ deepgram  │ Yes        │ 2      │
│ openai    │ Yes        │ 3      │
│ cartesia  │ Yes        │ 1      │
│ whisper   │ Yes        │ 1      │
│ ollama    │ Yes        │ 1      │
└───────────┴────────────┴────────┘
```

### Filter by project

```bash
voicegw status --project tonys-pizza
```

Displays the same table but with the project name in the header.
Returns an error if the project id does not exist.

### Use a specific config file

```bash
voicegw status --config /etc/voicegateway/voicegw.yaml
```

### Check for missing API keys

```bash
voicegw status
```

Look for providers showing `No API key`. Those need credentials
before they can serve requests.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Config failed to load, or the specified project was not found. |

## `voicegw doctor` {#doctor}

For a deeper check, run `voicegw doctor`. It runs a numbered punch
list of checks (config loads, providers configured, daemon up,
dashboard reachable, smoke test passes, secret-key set if managed
providers exist, etc.) and prints a fix step for each failure. No
stack traces. No bare "see docs" pointers.

```bash
voicegw doctor
```

## Related commands

- [`voicegw init`](/docs/cli/init): create a config file if you do
  not have one.
- [`voicegw onboard`](/docs/cli/onboard): five-question wizard.
- [`voicegw costs`](/docs/cli/costs): see what the providers are
  costing you.
- [`voicegw projects`](/docs/cli/projects): list all configured
  projects.
- [`voicegw smoke-test`](/docs/cli/smoke-test): exercise the
  inference pipeline end-to-end without LiveKit.
