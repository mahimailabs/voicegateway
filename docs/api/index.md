---
title: API Reference
description: VoiceGateway exposes three distinct API surfaces. Choose the one that fits your integration point.
---

VoiceGateway exposes three distinct API surfaces, each designed for a different integration point.

<CardGroup cols={2}>
  <Card title="Python SDK" icon="python" href="/api/python-sdk">
    `attach`, `guard`, and `Observer`. The public Python surface for wiring cost tracking and fallback into your LiveKit or Pipecat agent.
  </Card>
  <Card title="HTTP API" icon="server" href="/api/http-api">
    REST endpoints served by `voicegw serve` on port 8080. Observability, project CRUD, rated billing usage, Prometheus metrics.
  </Card>
  <Card title="Dashboard API" icon="chart-bar" href="/api/dashboard-api">
    Read-only `/api/*` endpoints served alongside the HTTP API, consumed by the built-in React dashboard.
  </Card>
  <Card title="MCP Server" icon="plug" href="/mcp/index">
    Model Context Protocol tools for AI coding agents. Add, rotate, and inspect providers, models, and costs from Claude Code or any MCP-compatible host.
  </Card>
</CardGroup>

## Choosing the right surface

| Use case | API surface |
|---|---|
| Wire cost tracking into a Python agent | [Python SDK](/api/python-sdk) |
| Manage providers, models, and projects remotely | [HTTP API](/api/http-api) |
| Build a custom dashboard or integrate with monitoring | [HTTP API](/api/http-api) |
| Pull rated billing usage and margin per tenant | [HTTP API](/api/http-api) (`/v1/billing/usage`) |
| Scrape Prometheus-format metrics | [HTTP API](/api/http-api) (`/v1/metrics`) |
| Use the built-in web dashboard | [Dashboard API](/api/dashboard-api) (automatic, served by the daemon) |
| Integrate with AI coding agents | [MCP server](/mcp/index) |
| Understand the system internals | [Architecture](/architecture/index) |

## Quick orientation

The Python SDK wraps your agent at construction time via `attach` and `guard`. You do not call the HTTP API from agent code; the daemon's middleware pipeline writes to SQLite and the HTTP and Dashboard APIs read from it.

```
Agent code
  -> attach(session, ...) / guard(provider, ...)
  -> middleware pipeline (cost tracking, rate limiting, fallback)
  -> SQLite
  -> HTTP API (/v1/*) + Dashboard API (/api/*)
  -> Dashboard UI + external monitoring
```

See [Architecture](/architecture/index) and [Core concepts](/guide/what-is-voicegateway) for a deeper walkthrough.
