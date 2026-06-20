---
title: "Deployment"
description: "Self-host the VoiceGateway fleet collector on a VPS, Railway, or Fly.io: Postgres-backed, HTTPS ingest, dashboard."
---

# Deploy the fleet collector

The collector is a single container that serves `POST /v1/ingest` (agents push cost telemetry, Bearer-keyed), the dashboard, and the cost API on port 8080. It needs Postgres, a public HTTPS URL, and an ingest key you create.

Request flow: `agent -> POST /v1/ingest (Bearer key) -> collector -> Postgres -> dashboard`.

## Pick a platform

| Platform | Ops effort | HTTPS | Postgres | Cost | Best when |
|---|---|---|---|---|---|
| [VPS](/docs/deployment/vps) | Highest (you run TLS) | You set up (Caddy) | Self-run or Neon | Cheapest | You already run a box (e.g. your LiveKit server) |
| [Railway](/docs/deployment/railway) | Lowest | Automatic | One-click managed | Higher | You want zero ops |
| [Fly.io](/docs/deployment/fly) | Low | Automatic | Fly Postgres or Neon | Low | Multi-region, near your agents |

For a single-node / SQLite setup (just yourself, no fleet), see [Docker deployment](/docs/examples/docker-deployment) instead.

## Connect your agent

Once the collector has a public HTTPS URL and you have the ingest key, point your agents at it. The explicit form:

```python
from openrtc import AgentPool
from voicegateway.openrtc import VoiceGatewayObserver

pool = AgentPool(observers=[VoiceGatewayObserver(
    project="prod",
    collector_url="https://<your-collector-url>",
    virtual_key="<your-ingest-key>",
)])
```

The env-var form: set `VOICEGW_COLLECTOR_URL` and `VOICEGW_VIRTUAL_KEY` in the agent's environment and use `VoiceGatewayObserver(project="prod")`. Non-OpenRTC agents call `voicegateway.attach(session, collector_url=..., virtual_key=...)` instead.

## Verify

Replace `<url>` and `<key>` with your collector's public URL and ingest key:

```bash
curl -fsS https://<url>/health && echo                 # -> ok

# auth gates ingest: no key is rejected, the key is accepted
curl -s -o /dev/null -w "no-key:  %{http_code}  (expect 401/403)\n" \
  -X POST https://<url>/v1/ingest -H 'content-type: application/json' -d '[]'
curl -s -o /dev/null -w "with-key: %{http_code}  (expect 2xx)\n" \
  -X POST https://<url>/v1/ingest -H 'content-type: application/json' \
  -H 'Authorization: Bearer <key>' -d '[]'
```

Then open the collector URL in a browser for the dashboard.

## Notes

::: note
The Postgres collector path works as of v0.9.2 (pin `>= 0.9.2`; the image is `mahimairaja/voicegateway:0.9.2`). The ingest key must not start with `vk_`. Only `/v1/ingest` and `/health` need to be public; all other endpoints can be firewall-restricted to your internal network.
:::
