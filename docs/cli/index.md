# CLI Reference

VoiceGateway ships a command-line interface called `voicegw`. It is the primary way to initialize configuration, start servers, and inspect gateway state from the terminal.

## Installation

The CLI is installed automatically with the package:

```bash
pip install voicegateway
```

After installation, the `voicegw` command is available globally.

## Subcommands

| Command | Description |
|---|---|
| [`init`](/cli/init) | Create a `voicegw.yaml` configuration template |
| [`status`](/cli/status) | Show provider configuration status |
| [`costs`](/cli/costs) | Display cost summaries |
| [`projects`](/cli/projects) | List configured projects |
| [`project <id>`](/cli/projects#project-detail) | Show details for a single project |
| [`logs`](/cli/logs) | Show recent request logs |
| [`serve`](/cli/serve) | Start the HTTP API server |
| [`dashboard`](/cli/dashboard) | Start the web dashboard |
| [`mcp`](/cli/mcp) | Start the MCP server for AI agents |

## Global Behavior

- Running `voicegw` with no arguments displays the help message.
- Most commands accept `--config` (`-c`) to specify a custom path to `voicegw.yaml`. If omitted, the gateway searches for the config file in the standard locations: `./voicegw.yaml`, `~/.config/voicegateway/voicegw.yaml`, `/etc/voicegateway/voicegw.yaml`.
- The CLI uses [Rich](https://rich.readthedocs.io/) for formatted terminal output (tables, panels, colored text).
- Commands that need cost or log data require `cost_tracking.enabled: true` in the config (which activates the SQLite backend).

## Quick Start

```bash
# 1. Create a config file
voicegw init

# 2. Edit it with your API keys
$EDITOR voicegw.yaml

# 3. Check provider status
voicegw status

# 4. Start the API server
voicegw serve

# 5. Start the dashboard in another terminal
voicegw dashboard
```
