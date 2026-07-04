---
title: CLI reference
description: Every voicegw command, what it does, and where to read more.
---

# CLI reference

VoiceGateway ships a command-line interface called `voicegw`. It is
the primary way to onboard, run the daemon, inspect gateway state,
and reconcile costs.

## Installation

The CLI is installed automatically with the package:

```bash
pipx install 'voicegateway[cloud,dashboard]'
```

After installation, the `voicegw` command is available globally. See
[Installation](/guide/installation) for every install variant
(curl-bash, pipx, uv, Docker, source).

## Subcommands

### Onboarding and lifecycle

| Command | Description |
|---|---|
| [`onboard`](/cli/onboard) | Five-question wizard. Writes config, installs and starts the daemon. |
| [`init`](/cli/init) | Scaffold a `voicegw.yaml` from the bundled template. |
| [`serve`](/cli/serve) | Run the daemon in the foreground. |
| [`start`](/cli/serve#start) | Start the OS-managed daemon. |
| [`stop`](/cli/serve#stop) | Stop the OS-managed daemon. |
| [`restart`](/cli/serve#restart) | Restart the OS-managed daemon. |
| [`daemon-logs`](/cli/serve#daemon-logs) | Tail the OS-native daemon log stream. |
| [`uninstall-daemon`](/cli/serve#uninstall) | Remove the daemon registration (config + db preserved). |

### Inspect and verify

| Command | Description |
|---|---|
| [`status`](/cli/status) | Provider configuration status. |
| [`doctor`](/cli/status#doctor) | Numbered punch list with fix steps. |
| [`smoke-test`](/cli/smoke-test) | Verify the inference pipeline end-to-end without LiveKit. |
| [`livekit`](/cli/livekit) | Diagnostics against a LiveKit server: agents, latency, SFU health, combined check. |
| [`logs`](/cli/logs) | Recent request logs. |
| [`costs`](/cli/costs) | Cost summaries per provider, project, and period. |
| [`projects`](/cli/projects) | List configured projects. |
| [`dashboard`](/cli/dashboard) | Open the dashboard in your browser. |
| [`tui`](/cli/tui) | Launch the terminal UI (status, costs, sessions). |
| [`replay`](/cli/replay) | Print the dashboard replay URL for a session. |

### Reconciliation and export

| Command | Description |
|---|---|
| [`reconcile`](/cli/reconcile) | Diff logged costs against a provider invoice. |
| [`export-costs`](/cli/export-costs) | Export per-request cost rows to CSV / JSONL. |

### MCP server

| Command | Description |
|---|---|
| [`mcp`](/cli/mcp) | Run the MCP server (stdio or HTTP/SSE) so coding agents can manage the gateway. |

## Global behaviour

- Running `voicegw` with no arguments displays the help menu.
- Most commands accept `--config` (`-c`) to specify a custom path to
  `voicegw.yaml`. If omitted, the gateway searches in this order:
  `./voicegw.yaml`, `~/.config/voicegateway/voicegw.yaml`,
  `/etc/voicegateway/voicegw.yaml`. The `VOICEGW_CONFIG` environment
  variable overrides the search.
- Commands that need cost or log data require
  `cost_tracking.enabled: true` in the config (which activates the
  SQLite backend), or the `VOICEGW_DB_PATH` env var pointing at a
  database path.
- The CLI uses [Rich](https://rich.readthedocs.io/) for formatted
  terminal output (tables, panels, coloured text).

## Quick start

```bash
# 1. Run the wizard
voicegw onboard

# 2. Verify state
voicegw status
voicegw doctor

# 3. Open the dashboard
voicegw dashboard
```

For the full first-run walkthrough see
[Get started](/get-started).
