---
title: CLI reference
description: Every voicegw command, what it does, and where its page lives now.
---
VoiceGateway ships a command-line interface called `voicegw`. Its commands
are documented across five nav tabs (Agents, SFU, SIP, Manage & Deploy,
Reference), grouped by what they're for rather than by CLI structure. This
page is the one flat lookup surface for all of them.

## Installation

```bash
pipx install 'voicegateway[livekit,dashboard]'
```

Every `voicegw` subcommand needs the `livekit` extra: the CLI package imports
the LiveKit admin client at module load, regardless of which command you run.
`serve`, `start`, `dashboard`, and `mcp` also need `dashboard` (FastAPI,
uvicorn). See [Installation](/guide/installation) for the full extras matrix
(`livekit`, `pipecat`, `dashboard`, `collector`) and every install variant
(curl-bash, pipx, uv, Docker, source).

## Commands

| Command | What it does | Docs |
|---|---|---|
| `voicegw onboard` | Four-question wizard: project, storage, port, daemon. Writes config and (optionally) installs and starts the daemon. | [cli/onboard](/cli/onboard) |
| `voicegw init` | Scaffold a `voicegw.yaml` from the bundled template. | [cli/init](/cli/init) |
| `voicegw serve` | Run the daemon in the foreground: HTTP API, dashboard, and MCP server on one port. | [cli/serve](/cli/serve) |
| `voicegw start` | Start the OS-managed daemon. | [cli/serve#voicegw-start](/cli/serve#voicegw-start) |
| `voicegw stop` | Stop the OS-managed daemon. | [cli/serve#voicegw-stop](/cli/serve#voicegw-stop) |
| `voicegw restart` | Restart the OS-managed daemon. | [cli/serve#voicegw-restart](/cli/serve#voicegw-restart) |
| `voicegw daemon-logs` | Tail the OS-native daemon log stream. | [cli/serve#voicegw-daemon-logs](/cli/serve#voicegw-daemon-logs) |
| `voicegw uninstall-daemon` | Remove the daemon registration (config and database preserved). | [cli/serve#voicegw-uninstall-daemon](/cli/serve#voicegw-uninstall-daemon) |
| `voicegw dashboard` | Open the dashboard in your browser. Starts nothing; the daemon already serves it. | [cli/dashboard](/cli/dashboard) |
| `voicegw status` | Provider configuration status. | [cli/status](/cli/status) |
| `voicegw doctor` | Numbered punch list with fix steps (config, providers, daemon, dashboard, smoke test, secret key). | [cli/status#voicegw-doctor](/cli/status#voicegw-doctor) |
| `voicegw check` | Verify metering and storage end to end by driving one synthetic request. | [cli/check](/cli/check) |
| `voicegw livekit agents` | List agents currently in LiveKit rooms. | [cli/livekit](/cli/livekit#voicegw-livekit-agents) |
| `voicegw livekit latency` | Probe an agent and report its reply latency split. | [cli/livekit](/cli/livekit#voicegw-livekit-latency) |
| `voicegw livekit sfu` | Probe SFU node health (single- or multi-vantage). | [cli/livekit](/cli/livekit#voicegw-livekit-sfu) |
| `voicegw livekit check` | Run the combined gate (agents/sfu/sfu_load/latency) and exit non-zero on failure, for CI. | [cli/livekit](/cli/livekit#voicegw-livekit-check) |
| `voicegw livekit report` | Export a recorded diagnostics run as a report, without probing anything. | [cli/livekit](/cli/livekit#voicegw-livekit-report) |
| `voicegw logs` | Recent request logs, filterable by project and modality. | [cli/logs](/cli/logs) |
| `voicegw costs` | Cost summary by provider and model for a period. | [cli/costs](/cli/costs) |
| `voicegw calls` | Recent calls with per-call cost and activity. | [cli/calls](/cli/calls) |
| `voicegw replay` | Print the dashboard replay URL for a session (needs `attach(snapshots=True)`). | [cli/replay](/cli/replay) |
| `voicegw reconcile` | Diff recorded costs against a provider's usage export. | [cli/reconcile](/cli/reconcile) |
| `voicegw export-costs` | Export per-request cost line items as CSV or JSON. | [cli/export-costs](/cli/export-costs) |
| `voicegw prices ls` | Print the billing rate card in effect (default markup + rules). | [cli/prices](/cli/prices#voicegw-prices-ls) |
| `voicegw prices reconcile` | Roll up rated revenue vs. recorded cost per tenant; flag thin or negative margins. | [cli/prices](/cli/prices#voicegw-prices-reconcile) |
| `voicegw prices sync` | Check fixed ($/unit) rate rules against the current voice-prices base cost. | [cli/prices](/cli/prices#voicegw-prices-sync) |
| `voicegw prices set` | Add or update a rate-card rule. | [cli/prices](/cli/prices#voicegw-prices-set) |
| `voicegw prices rm` | Remove a rate-card rule. | [cli/prices](/cli/prices#voicegw-prices-rm) |
| `voicegw loadtest import` | Ingest an external SIP load generator's run artifacts. | [cli/loadtest](/cli/loadtest#import) |
| `voicegw loadtest runs` | List imported load-test runs. | [cli/loadtest](/cli/loadtest#runs) |
| `voicegw loadtest report` | Render a capacity report from an imported run, correlated with node metrics. | [cli/loadtest](/cli/loadtest#report) |
| `voicegw projects` | List all configured projects. | [configuration/projects](/configuration/projects#cli) |
| `voicegw project <id>` | Show one project's details and today's spend. | [configuration/projects](/configuration/projects#cli) |
| `voicegw mcp` | Run the MCP server (stdio or HTTP/SSE) so coding agents can manage the gateway. | [cli/mcp](/cli/mcp) |

### Admin and operator commands without a reference page yet

These exist and work; they don't have a dedicated docs page in this tab yet.

| Command | What it does |
|---|---|
| `voicegw brand set` / `clear` / `show` | Scripted per-project white-label provisioning: logo, accent color, product name, via the dashboard's branding API. |
| `voicegw route show <project>` | Read-only routing diagnostics: the latency-observation rollup and rosters a project's fallback routing uses. |
| `voicegw route simulate <project>` | Dry-run which provider triple routing would currently pick, without placing a call. |
| `voicegw rotate-secret` | Re-encrypt managed provider API keys after rotating `VOICEGW_SECRET` (needs `VOICEGW_SECRET` and `VOICEGW_SECRET_FALLBACK` set). |
| `voicegw migrate` | Migrate a v0.0.5 install's config layout into the current one. |

`voicegw check` also has a hidden, deprecated alias `smoke-test` that behaves identically; it's omitted from `--help`.

## Global behaviour

- Running `voicegw` with no arguments displays the help menu.
- Most commands accept `--config` (`-c`) to specify a custom path to
  `voicegw.yaml`. If omitted, the gateway searches in this order:
  `./voicegw.yaml`, `~/.config/voicegateway/voicegw.yaml`,
  `/etc/voicegateway/voicegw.yaml`. The `VOICEGW_CONFIG` environment
  variable is used instead of that search when `--config` is not passed.
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
