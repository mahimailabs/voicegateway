---
title: Agent Setup
description: Register VoiceGateway's MCP server in Claude Code, Cursor, or Codex, plus example prompts for the default tool surface.
---

## Prerequisites

<CodeGroup>
```bash pip
pip install "voicegateway[dashboard,livekit]"
```
```bash uv
uv add "voicegateway[dashboard,livekit]"
```
</CodeGroup>

The MCP server ships inside the `dashboard` extra; there is no separate `mcp` extra. `livekit` is required too, even for Pipecat-only agents: every `voicegw` subcommand imports it at CLI startup. See [Installation](/guide/installation) for the full extras matrix.

## Claude Code

Add to `.mcp.json` in your project root, or your user-scoped Claude config:

```json
{
  "mcpServers": {
    "voicegateway": {
      "command": "voicegw",
      "args": ["mcp", "--transport", "stdio"]
    }
  }
}
```

With a non-default config file:

```json
{
  "mcpServers": {
    "voicegateway": {
      "command": "voicegw",
      "args": ["mcp", "--transport", "stdio", "--config", "/path/to/voicegw.yaml"]
    }
  }
}
```

If VoiceGateway is installed in a virtualenv that isn't on `PATH`, point `command` at the full binary path instead (e.g. `/path/to/venv/bin/voicegw`).

Restart Claude Code after saving. It connects over stdio and lists the ten default tools (25 with `VOICEGW_MCP_ADMIN=1` set on the server). See [Default tools](/mcp/tools).

## Cursor

Add the same `command`/`args` shape to `.cursor/mcp.json` (project) or the global Cursor MCP settings for stdio, or point at a shared HTTP/SSE server:

```json
{
  "mcpServers": {
    "voicegateway": {
      "url": "http://your-server:8090/sse",
      "headers": { "Authorization": "Bearer your-token-here" }
    }
  }
}
```

See [Transports & auth](/mcp/transports) for running the server that way and securing it.

## Codex (OpenAI CLI)

Codex speaks MCP over stdio with the same config shape as Claude Code above.

## Example prompts

Against the default tool surface (`VOICEGW_MCP_ADMIN` unset):

| Prompt | Tool called |
|---|---|
| "Check VoiceGateway health" | `get_health` |
| "What's tonys-pizza spending this week?" | `get_costs` with `period="week", project="tonys-pizza"` |
| "Create a project called demo with a $5 daily budget" | `create_project` |
| "Show me the last 20 errors" | `get_logs` with `status="error", limit=20` |
| "What models can I route to?" | `list_models` |

With `VOICEGW_MCP_ADMIN=1` the agent can also add and test provider credentials and delete resources; see [Admin tools](/mcp/tools-admin).

## Next steps

- [Transports & auth](/mcp/transports): stdio vs. HTTP/SSE, and securing the HTTP transport.
- [CLI reference: mcp](/cli/mcp): all flags for `voicegw mcp`.
