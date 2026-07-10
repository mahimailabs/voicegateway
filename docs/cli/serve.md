---
title: voicegw serve / start / stop / restart
description: Run the VoiceGateway daemon. serve runs in the foreground; start, stop, restart, daemon-logs, and uninstall-daemon manage the OS-installed service.
---

# voicegw serve / start / stop / restart

The daemon is the single long-lived process behind VoiceGateway. It serves the HTTP API (`/v1/*`), the dashboard API (`/api/*`), and the React SPA (`/`) on a single port. Five lifecycle commands manage it.

## `voicegw serve`

Run the daemon in the foreground. Useful for development, smoke testing, and Docker entrypoints.

### Usage

```bash
voicegw serve [OPTIONS]
```

### Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | `string` | auto | Path to `voicegw.yaml`. Auto-discovered if omitted. |
| `--host` | | `string` | `serve.host` or `0.0.0.0` | Bind address. Override with `127.0.0.1` to restrict to localhost. |
| `--port` | | `integer` | `serve.port` or `8080` | Port number to listen on. |

### Behaviour

1. Load the gateway configuration.
2. Build the FastAPI app: registers all `/v1/*` routers, all `/api/*` dashboard routers, mounts the React SPA at `/`, mounts the branding directory at `/static/branding/*`, and wires the MCP SSE transport.
3. Start uvicorn on the resolved host and port.

The server runs in the foreground; stop with Ctrl+C. For background operation use `voicegw start` (after `voicegw onboard` has installed the daemon) or run inside a process supervisor.

### Examples

```bash
# Default bind (uses serve.host / serve.port from config)
voicegw serve

# Override the port for local testing
voicegw serve --port 8090

# Bind to localhost only
voicegw serve --host 127.0.0.1
```

## `voicegw start`

Bring the OS-installed daemon up. The daemon must be installed first (via `voicegw onboard` or `voicegw onboard --install-daemon`).

```bash
voicegw start
```

On macOS this calls `launchctl bootstrap`; on Linux, `systemctl --user start`; on Windows, `schtasks /Run`.

Exits 0 on success. Exits 1 if the OS service manager refuses (usually because the service is not installed).

## `voicegw stop`

Bring the OS-installed daemon down.

```bash
voicegw stop
```

## `voicegw restart`

Stop, then start. Equivalent to `voicegw stop && voicegw start` but does both inside one call to the service manager so race conditions cannot leave the daemon in a half-down state.

```bash
voicegw restart
```

## `voicegw daemon-logs`

Tail the OS-native daemon log stream.

```bash
voicegw daemon-logs [--tail N]
```

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--tail` | `-n` | `integer` | `100` | Number of recent log lines to print. |

On macOS this reads `~/Library/Logs/voicegateway/*.log`. On Linux, `journalctl --user -u voicegateway`. On Windows, the Task Scheduler event log.

## `voicegw uninstall-daemon`

Remove the daemon registration. The config file at `~/.config/voicegateway/voicegw.yaml` and the SQLite database (`~/.config/voicegateway/voicegw.db` by default) are preserved.

```bash
voicegw uninstall-daemon
```

The command prints what was removed and what was preserved, plus the manual `rm -rf` command if you want to wipe state too.

## Prerequisites

The `dashboard` extra must be installed so `uvicorn` is on the import path:

```bash
pipx install 'voicegateway[cloud,dashboard]'
# or
pip install 'voicegateway[cloud,dashboard]'
```

If `uvicorn` is missing, `voicegw serve` exits with an error message pointing at this install command.

## Docker

The `serve` command is the default entrypoint in the Docker image:

```bash
docker compose up -d
```

The container binds port 8080 by default (override via `VOICEGW_PORT`).

## Related

[`voicegw onboard`](/cli/onboard) | [`voicegw dashboard`](/cli/dashboard) | [`voicegw status`](/cli/status) | [`voicegw mcp`](/cli/mcp)
