# API Reference

VoiceGateway exposes three distinct API surfaces, each designed for a different integration point.

## Python SDK

The **Gateway class** is the primary programmatic interface. Import it directly into your Python application to route STT, LLM, and TTS requests through the gateway with full middleware support (cost tracking, latency monitoring, fallback chains, budget enforcement).

```python
from voicegateway import Gateway

gw = Gateway()
stt = gw.stt("deepgram/nova-3", project="my-app")
llm = gw.llm("openai/gpt-4o-mini", project="my-app")
tts = gw.tts("cartesia/sonic-3", project="my-app")
```

Best for: application code, scripts, Jupyter notebooks, custom integrations.

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

The **Dashboard API** is a separate FastAPI application that runs on port 9090 (default) via `voicegw dashboard`. It serves the React frontend and exposes a smaller set of read-only `/api/*` endpoints optimized for the dashboard UI. These endpoints aggregate data slightly differently from the HTTP API (e.g., `/api/overview` combines multiple queries into a single response).

```bash
curl http://localhost:9090/api/overview
curl http://localhost:9090/api/costs?period=today
```

Best for: the built-in web dashboard (consumed automatically).

[Full Dashboard API reference](/api/dashboard-api)

## Choosing the Right API

| Use case | API surface |
|---|---|
| Route voice AI requests in Python | Python SDK |
| Manage providers/models/projects remotely | HTTP API |
| Build a custom dashboard or integrate with monitoring | HTTP API |
| Use the built-in web UI | Dashboard API (automatic) |
| Integrate with AI coding agents | [MCP server](/mcp/) |
