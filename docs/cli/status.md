---
title: voicegw status
description: Show provider configuration status. Useful for verifying setup after editing voicegw.yaml or adding providers via the API.
---
Show the configuration status of all providers.

## Synopsis

`voicegw status` displays a table of every provider defined in the config, whether it has credentials configured, and how many models are registered against it. This is the quickest way to verify your setup after editing `voicegw.yaml` or adding providers via the API.

## Usage

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

```bash
# Show all provider status
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

```bash
# Filter by project
voicegw status --project tonys-pizza
```

```bash
# Use a specific config file
voicegw status --config /etc/voicegateway/voicegw.yaml
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Config failed to load, or the specified project was not found. |

## `voicegw doctor`

For a deeper check, run `voicegw doctor`. It runs a numbered punch list of checks (config loads, providers configured, daemon up, dashboard reachable, smoke test passes, secret-key set if managed providers exist, etc.) and prints a fix step for each failure. No stack traces. No bare "see docs" pointers.

```bash
voicegw doctor
```

## Related

[`voicegw init`](/cli/init) | [`voicegw onboard`](/cli/onboard) | [`voicegw costs`](/cli/costs) | [`voicegw projects`](/configuration/projects) | [`voicegw check`](/cli/check)
