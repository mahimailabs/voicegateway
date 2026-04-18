# voicegw dashboard

Start the VoiceGateway web dashboard.

## Purpose

The `dashboard` command launches a FastAPI server that serves the React frontend along with the Dashboard API endpoints (`/api/*`). The dashboard provides a visual interface for monitoring costs, latency, provider status, and project health.

## Syntax

```bash
voicegw dashboard [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | `string` | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |
| `--host` | | `string` | `0.0.0.0` | Bind address. |
| `--port` | | `integer` | `9090` | Port number to listen on. |

## Prerequisites

1. The `dashboard` extra must be installed:

```bash
pip install "voicegateway[dashboard]"
```

2. The frontend must be built (for the full UI experience):

```bash
cd dashboard/frontend
npm install && npm run build
```

If the frontend is not built, the dashboard API endpoints still work, but `GET /` returns a JSON error with build instructions instead of the React app.

## Behavior

1. Loads the gateway configuration.
2. Injects the gateway instance into the dashboard FastAPI app.
3. Serves the built React frontend from `dashboard/frontend/dist/`.
4. All `/api/*` routes serve JSON data for the dashboard.
5. All other routes fall through to `index.html` for client-side routing (SPA).

The server runs in the foreground and can be stopped with `Ctrl+C`.

## Examples

### Start on default port

```bash
voicegw dashboard
```

Opens the dashboard at `http://0.0.0.0:9090`.

### Start on a custom port

```bash
voicegw dashboard --port 3001
```

### Bind to localhost only

```bash
voicegw dashboard --host 127.0.0.1
```

### Run alongside the API server

```bash
# Terminal 1: API server
voicegw serve --port 8080

# Terminal 2: Dashboard
voicegw dashboard --port 9090
```

## Docker

In the Docker Compose setup, both the API and dashboard run as separate services:

```bash
docker compose up -d
```

## Related Commands

- [`voicegw serve`](/cli/serve) -- the HTTP API server (consumed by the dashboard)
- [`voicegw status`](/cli/status) -- quick terminal-based status check
- [`voicegw costs`](/cli/costs) -- terminal-based cost summary
