# voicegw serve

Start the VoiceGateway HTTP API server.

## Purpose

The `serve` command launches a FastAPI server that exposes the full HTTP API, including CRUD operations for providers, models, and projects, plus observability endpoints for costs, latency, logs, and Prometheus metrics. The dashboard frontend and external monitoring tools consume this API.

## Syntax

```bash
voicegw serve [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | `string` | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |
| `--host` | | `string` | `0.0.0.0` | Bind address. Use `127.0.0.1` to restrict to localhost. |
| `--port` | | `integer` | `8080` | Port number to listen on. |

## Prerequisites

Requires the `dashboard` extra to be installed (for `uvicorn`):

```bash
pip install "voicegateway[dashboard]"
```

If `uvicorn` is not installed, the command prints an error message with installation instructions and exits.

## Behavior

1. Loads the gateway configuration from the specified (or auto-discovered) config file.
2. Builds the FastAPI application bound to the gateway instance.
3. Starts a uvicorn server on the specified host and port.
4. CORS is enabled for all origins (suitable for development; restrict in production via a reverse proxy).

The server runs in the foreground and can be stopped with `Ctrl+C`.

## Examples

### Start on default port

```bash
voicegw serve
```

Starts the API at `http://0.0.0.0:8080`.

### Start on a custom port

```bash
voicegw serve --port 3000
```

### Bind to localhost only

```bash
voicegw serve --host 127.0.0.1 --port 8080
```

### Use a specific config file

```bash
voicegw serve --config /etc/voicegateway/voicegw.yaml --port 8080
```

## Docker

The `serve` command is the default entrypoint in the Docker image:

```bash
docker compose up -d
```

This starts the API server on port 8080 inside the container.

## Related Commands

- [`voicegw dashboard`](/cli/dashboard) -- start the web UI (separate server)
- [`voicegw mcp`](/cli/mcp) -- start the MCP server for AI agents
- [`voicegw status`](/cli/status) -- verify config before starting
