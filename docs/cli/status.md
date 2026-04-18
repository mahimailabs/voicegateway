# voicegw status

Show the configuration status of all providers.

## Purpose

The `status` command displays a table of every provider defined in the config, whether it has credentials configured, and how many models are registered against it. This is useful for verifying your setup after editing `voicegw.yaml` or adding providers via the API.

## Syntax

```bash
voicegw status [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | `string` | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |
| `--project` | `-p` | `string` | `null` | Filter the display to a specific project. Validates the project ID exists in the config. |

## Output

A Rich-formatted table with columns:

| Column | Description |
|---|---|
| **Provider** | Provider name (e.g., `deepgram`, `openai`, `whisper`). |
| **Configured** | `Yes` if the provider has an API key or is a local provider; `No API key` otherwise. |
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

### Show status with a specific config file

```bash
voicegw status --config /etc/voicegateway/voicegw.yaml
```

### Filter by project

```bash
voicegw status --project tonys-pizza
```

Displays the same table but with the project name in the header. Returns an error if the project ID does not exist.

### Check for missing API keys

```bash
voicegw status -c ./voicegw.yaml
```

Look for providers showing `No API key` -- those need credentials before they can serve requests.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Config failed to load, or the specified project was not found. |

## Related Commands

- [`voicegw init`](/cli/init) -- create a config file if you do not have one
- [`voicegw costs`](/cli/costs) -- see what the providers are costing you
- [`voicegw projects`](/cli/projects) -- list all configured projects
