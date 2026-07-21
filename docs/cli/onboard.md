---
title: voicegw onboard
description: Four-question wizard that writes voicegw.yaml, registers the daemon, and prints the one line to add to your agent.
---

# voicegw onboard

Four-question wizard that gets VoiceGateway running from a fresh install.

## Synopsis

`voicegw onboard` is the recommended first-run command. VoiceGateway is framework-agnostic: it meters the native provider instances you build in your agent (via `attach()`), so onboarding does not ask for a provider or an API key. It collects your project, storage location, port, and daemon preference, writes the config, optionally registers the daemon, prints the one line to add to your agent, and runs a `check`.

## Usage

```bash
voicegw onboard [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | `string` | `~/.config/voicegateway/voicegw.yaml` | Path to the `voicegw.yaml` to create or update. |
| `--install-daemon` / `--no-install-daemon` | | `flag` | (prompted) | Register the daemon with the OS service manager. Omit to be prompted (default in the prompt: yes). |

## The four questions

| # | Question | Default | Notes |
|---|---|---|---|
| 1 | Project name | `default` | Becomes the key under `projects:` and the top-level `default_project`. |
| 2 | Storage: SQLite db path | (default path) | Blank uses `~/.config/voicegateway/voicegw.db`. |
| 3 | Port for `voicegw serve` | `8080` | The daemon binds this port for `/v1/*`, `/api/*`, and the dashboard SPA at `/`. |
| 4 | Install daemon? | `yes` | Skipped when `--install-daemon` or `--no-install-daemon` is passed. |

No provider or key: VoiceGateway never writes a secret to `voicegw.yaml`. You install your provider plugins in your agent and pass the instances to `attach()` / `guard()`.

## Behaviour

1. Resolves the config path (`--config`, else the default).
2. Reads the existing config if present so the wizard merges without clobbering hand-edited keys.
3. Prompts the questions (or three if a `--install-daemon` flag was already passed).
4. Writes the merged config: `projects.<name>`, top-level `default_project`, `cost_tracking.enabled: true` (plus `db_path` if you set one), and `serve.port`. No `providers:` block, no key.
5. If `install_daemon` is yes: registers the OS-specific service (launchd / systemd / Scheduled Task) to run `voicegw serve -c <config path>` against the config just written, and starts the daemon. Re-running onboard replaces any prior registration cleanly.
6. Prints a summary plus the integration snippet:

   ```python
   import voicegateway
   voicegateway.attach(session, project="default")
   ```

   and, for fleet mode, the `VOICEGW_COLLECTOR_URL` / `VOICEGW_API_KEY` env vars.
7. Offers to run `voicegw check` against the new config (drives one synthetic request and confirms it lands in storage).

Pressing Ctrl+C at any point restores the prior `voicegw.yaml` (or deletes it if there was none) and exits with code 130.

## Examples

```bash
# Default flow: four questions, installs and starts the daemon, runs a check
voicegw onboard
```

```bash
# Skip the daemon prompt
voicegw onboard --no-install-daemon
```

```bash
# Write to a custom config path
voicegw onboard --config /etc/voicegateway/voicegw.yaml
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Write failure. |
| `130` | Ctrl+C cancellation. The prior config is restored. |

## Related

[`voicegw check`](/cli/check) | [`voicegw status`](/cli/status) | [`voicegw doctor`](/cli/status) | [`voicegw serve`](/cli/serve) | [`voicegw dashboard`](/cli/dashboard)
