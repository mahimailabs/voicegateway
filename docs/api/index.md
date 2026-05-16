# API Reference

VoiceGateway exposes three distinct API surfaces, each designed for a different integration point.

## Python SDK

The **`voicegateway.inference` module** is the public Python surface. It is a drop-in mirror of `livekit.agents.inference`: change the import line in an existing LiveKit Cloud Inference agent and the rest of the code keeps working. Cost tracking, latency monitoring, and session correlation happen transparently in the middleware.

```python
from livekit.agents import AgentSession

from voicegateway.inference import STT, LLM, TTS

session = AgentSession(
    stt=STT("deepgram/nova-3"),
    llm=LLM("openai/gpt-4.1-mini"),
    tts=TTS("cartesia/sonic-3"),
)
```

Best for: application code, scripts, custom integrations.

[Full Python SDK reference](/api/python-sdk)

## HTTP API

The **REST API** runs on port 8080 (default) via `voicegw serve`. It provides CRUD operations for providers, models, and projects, plus read-only endpoints for costs, latency, logs, and Prometheus-format metrics. The dashboard frontend consumes this API, and external monitoring tools can scrape `/v1/metrics`.

```bash
curl http://localhost:8080/v1/status
curl http://localhost:8080/v1/costs?period=week&project=my-app
```

Best for: dashboards, monitoring, CI/CD pipelines, multi-language teams.

[Full HTTP API reference](/api/http-api)

## Dashboard API

The **dashboard API** is mounted by the daemon (`voicegw serve`)
under the `/api/` prefix on the same port as the HTTP API and the
React SPA. It exposes a smaller set of read-only `/api/*` endpoints
optimised for the dashboard UI. These endpoints aggregate data
slightly differently from the HTTP API (for example, `/api/overview`
combines multiple queries into a single response).

```bash
curl http://localhost:8080/api/overview
curl http://localhost:8080/api/costs?period=today
```

Best for: the built-in web dashboard (consumed automatically).

[Full Dashboard API reference](/api/dashboard-api)

## Choosing the Right API

| Use case | API surface |
|---|---|
| Route voice AI requests in Python | Python SDK |
| Manage providers/models/projects remotely | HTTP API |
| Build a custom dashboard or integrate with monitoring | HTTP API |
| Use the built-in web UI | Dashboard API (automatic, served by the daemon) |
| Integrate with AI coding agents | [MCP server](/mcp/) |
