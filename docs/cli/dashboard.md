---
title: voicegw dashboard
description: Open the VoiceGateway dashboard in your browser. The daemon already serves it; this command just launches the URL.
---
Open the VoiceGateway dashboard in your browser.

## Synopsis

The daemon (started by `voicegw onboard` or `voicegw serve`) already serves the React dashboard at `/`, the dashboard API at `/api/*`, and the public HTTP API at `/v1/*` on the same port. `voicegw dashboard` does not start a second process. It resolves the daemon URL from your config and opens your browser at it.

## Usage

```bash
voicegw dashboard [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | `string` | auto | Path to `voicegw.yaml`. Auto-discovered if omitted. |
| `--no-open` | | `flag` | `false` | Print the dashboard URL without launching a browser. Useful over SSH. |

## Behaviour

1. Load the gateway configuration.
2. Resolve the host/port from `serve.host` and `serve.port` in `voicegw.yaml`, falling back to `0.0.0.0` and `8080`. The resolved URL replaces `0.0.0.0` with `localhost` because browsers do not handle bare `0.0.0.0` as a host.
3. Print the URL to the terminal.
4. Unless `--no-open` is set, call `webbrowser.open(url)`. If the browser auto-launch fails (no display, sandboxed environment), the command prints a warning and exits with status 0. The URL printed in step 3 is enough to copy and open manually.

The command exits as soon as the browser receives the URL. There is no foreground process to stop.

## Prerequisites

The daemon must be running. Onboarding installs and starts it by default; verify with `voicegw status`. If you skipped daemon install, start it in another shell first:

```bash
# Foreground (Ctrl+C to stop)
voicegw serve

# Background via OS service manager
voicegw start
```

The `dashboard` extra must be installed so the React bundle ships with the wheel:

```bash
pipx install 'voicegateway[dashboard]'
# or
pip install 'voicegateway[dashboard]'
```

## Examples

```bash
# Open the dashboard in your browser
voicegw dashboard
```

```bash
# Print the URL only (useful over SSH)
voicegw dashboard --no-open
```

```bash
# Use a non-default config
voicegw dashboard --config /etc/voicegateway/voicegw.yaml
```

## Related

[`voicegw serve`](/cli/serve) | [`voicegw start`](/cli/serve) | [`voicegw status`](/cli/status) | [`voicegw onboard`](/cli/onboard)
