---
title: voicegw dashboard
description: Open the VoiceGateway dashboard in your browser. The daemon already serves it; this command just launches the URL.
---

# voicegw dashboard

Open the VoiceGateway dashboard in your browser.

## Purpose

The daemon (started by `voicegw onboard` or `voicegw serve`) already
serves the React dashboard at `/`, the dashboard API at `/api/*`, and
the public HTTP API at `/v1/*` on the same port. `voicegw dashboard`
does not start a second process; it resolves the daemon URL from
your config and opens your browser at it.

## Syntax

```bash
voicegw dashboard [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | `string` | auto | Path to `voicegw.yaml`. Auto-discovered if omitted. |
| `--no-open` | | `flag` | `false` | Print the dashboard URL without launching a browser. Useful over SSH. |

## Behaviour

1. Load the gateway configuration (the same path `voicegw status`
   uses).
2. Resolve the host/port from `serve.host` / `serve.port` in
   `voicegw.yaml`, falling back to `0.0.0.0` and `8080`. The
   resolved URL replaces `0.0.0.0` with `localhost` because browsers
   do not handle bare `0.0.0.0` as a host.
3. Print the URL to the terminal.
4. Unless `--no-open` is set, call `webbrowser.open(url)`. If the
   browser auto-launch fails (no display, sandboxed environment),
   the command prints a warning and exits with status 0; the URL
   already printed in step 3 is enough to copy and open manually.

The command exits as soon as the browser receives the URL. There is
no foreground process to stop.

## Prerequisites

The daemon must be running. Onboarding installs and starts it by
default; verify with `voicegw status`. If you skipped daemon install,
start it in another shell first:

```bash
voicegw serve            # foreground; Ctrl+C to stop
voicegw start            # background (uses the OS service manager)
```

The `dashboard` extra must be installed at the package level so the
React bundle ships with the wheel:

```bash
pipx install 'voicegateway[dashboard]'
```

## Examples

### Open the dashboard

```bash
voicegw dashboard
```

Prints the URL and launches your browser.

### Print the URL only

```bash
voicegw dashboard --no-open
```

Useful when you are SSH'd into the host running the daemon and want
to copy the URL into a browser on your laptop (after setting up an
SSH tunnel).

### Use a non-default config

```bash
voicegw dashboard --config /etc/voicegateway/voicegw.yaml
```

Resolves host/port from that file. The daemon must already be bound
to the matching address; this command does not start the daemon.

## Related commands

- [`voicegw serve`](/cli/serve) starts the daemon in the foreground.
- `voicegw start` brings the OS-managed daemon up.
- [`voicegw status`](/cli/status) shows daemon and provider state in
  the terminal.
- [`voicegw onboard`](/cli/onboard) writes the config, installs the
  daemon, and prints the dashboard URL at the end.
