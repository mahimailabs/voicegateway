---
title: voicegw onboard
description: Five-question wizard that writes voicegw.yaml, registers the daemon with your OS service manager, and starts it.
---

# voicegw onboard

Five-question wizard that gets VoiceGateway running from a fresh
install.

## Purpose

`voicegw onboard` is the recommended first-run command. It:

- Collects project name, provider, API key, port, and a yes/no on
  daemon installation.
- Validates your API key against the provider (5-second timeout).
- Writes `~/.config/voicegateway/voicegw.yaml` (or the path you
  supply via `--config`).
- Registers the daemon with the OS service manager (LaunchAgent on
  macOS, `systemd --user` unit on Linux, Scheduled Task on Windows)
  and starts it.
- Optionally runs `voicegw smoke-test` and confirms the inference
  pipeline.
- Prints the dashboard URL and the next-step commands.

## Syntax

```bash
voicegw onboard [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | `string` | `~/.config/voicegateway/voicegw.yaml` | Path to the `voicegw.yaml` to create or update. |
| `--install-daemon` / `--no-install-daemon` | | `flag` | (prompted) | Register the daemon with the OS service manager. Omit to be prompted (default in the prompt: yes). |

## The five questions

| # | Question | Default | Notes |
|---|---|---|---|
| 1 | Project name | `default` | Becomes the key under `projects:` in the YAML. |
| 2 | Provider | `openai` | One of the known providers: openai, deepgram, anthropic, groq, cartesia, elevenlabs, assemblyai. |
| 3 | API key | (no default) | Pasted; hidden from terminal echo. Validated against the provider. |
| 4 | Port for `voicegw serve` | `8080` | The daemon binds this port for `/v1/*`, `/api/*`, and the dashboard SPA at `/`. |
| 5 | Install daemon? | `yes` | Skipped when `--install-daemon` / `--no-install-daemon` is passed. |

## Behaviour

1. Resolves the config path (`--config`, else the default).
2. Reads the existing config if one is present so the wizard can
   merge new providers without clobbering the rest.
3. Prompts five questions (or four if `--install-daemon` was
   already passed).
4. Validates the provider API key. On 5-second timeout the wizard
   continues with a warning instead of failing.
5. Writes the merged config to disk.
6. If `install_daemon` is yes: renders the OS-specific service
   definition (plist / unit / scheduled task), registers it,
   and starts the daemon.
7. Prints a summary table (project, provider, port, daemon status,
   dashboard URL) and the next-step commands
   (`voicegw status`, `voicegw doctor`, `voicegw stop`).
8. Prompts whether to run `voicegw smoke-test` against the new
   config.

Pressing Ctrl+C at any point restores the prior `voicegw.yaml` (or
deletes it if there was none) and exits with code 130.

## Examples

### Default flow

```bash
voicegw onboard
```

Walks through all five questions and installs + starts the daemon
when prompted.

### Skip the daemon prompt

```bash
voicegw onboard --no-install-daemon
```

Writes the config but does not register the daemon. You can install
it later with `voicegw onboard --install-daemon` or by re-running
the wizard.

### Write to a custom config path

```bash
voicegw onboard --config /etc/voicegateway/voicegw.yaml
```

Useful when the gateway runs as a system service reading
`/etc/voicegateway/voicegw.yaml`.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Validation or write failure. |
| `130` | Ctrl+C cancellation. The prior config is restored. |

## Related commands

- [`voicegw status`](/cli/status): verify the wizard wrote a
  working config.
- [`voicegw doctor`](/cli/status): numbered punch list when
  something is off.
- [`voicegw start`](/cli/serve): start the daemon manually if
  you skipped the install step.
- [`voicegw dashboard`](/cli/dashboard): open the dashboard URL
  in your browser.
