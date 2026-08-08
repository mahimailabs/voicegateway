---
title: "Deploy VoiceGateway"
description: "Ship the self-hosted VoiceGateway daemon to production: HTTP API and dashboard on one port, backed by Postgres or SQLite."
---
The VoiceGateway daemon is a single container that serves:

- `POST /v1/ingest` (agents push cost telemetry, Bearer-keyed)
- The web dashboard
- The cost and analytics API

Everything runs on port 8080. Your agents push to it; you read costs from the dashboard.

Request flow: `agent -> POST /v1/ingest (Bearer key) -> daemon -> database -> dashboard`.

## Prerequisites

- A server or managed platform with outbound internet access
- Docker (for VPS) or a platform account (Railway, Fly.io)
- A Postgres database (or SQLite for single-user local use)
- A public HTTPS URL so agents can reach `/v1/ingest`

The ingest key you create must not start with `vk_`. Generate one with:

```bash
openssl rand -hex 32
```

## Pick a platform

<CardGroup cols={3}>
  <Card title="Fly.io" icon="rocket" href="/deployment/fly">
    Low ops. Automatic HTTPS. Multi-region placement to sit near your agents.
  </Card>
  <Card title="Railway" icon="train" href="/deployment/railway">
    Zero ops. One-click Postgres. Automatic HTTPS and public URL.
  </Card>
  <Card title="VPS (systemd)" icon="server" href="/deployment/vps">
    Cheapest. Full control. Ideal for co-locating with a self-hosted LiveKit server.
  </Card>
</CardGroup>

<CardGroup cols={2}>
  <Card title="Auto-update" icon="arrows-rotate" href="/deployment/auto-update">
    Keep your self-hosted daemon on the latest patch release automatically.
  </Card>
  <Card title="Distributed SFU probers" icon="globe" href="/deployment/distributed-sfu">
    Load-test your LiveKit SFU from multiple regions simultaneously.
  </Card>
</CardGroup>

For a single-node / SQLite setup (just yourself, no fleet), see [Docker deployment](/examples/docker-deployment) instead.

## Connect your agent

Point your agents at the daemon's public HTTPS URL and the ingest key. Explicit form:

```python
from voicegateway import attach

# In your LiveKit agent session handler:
await attach(session, collector_url="https://<your-daemon-url>", api_key="<your-ingest-key>")
```

Env-var form: set `VOICEGW_COLLECTOR_URL` and `VOICEGW_API_KEY` on the agent, then call `attach(session)` with no extra arguments. Pass a project with `attach(session, project="prod")`.

## Verify

Replace `<url>` and `<key>` with your daemon's public URL and ingest key:

```bash
curl -fsS https://<url>/health && echo                 # -> ok

# Auth gates ingest: no key is rejected, the key is accepted.
curl -s -o /dev/null -w "no-key:  %{http_code}  (expect 401/403)\n" \
  -X POST https://<url>/v1/ingest -H 'content-type: application/json' -d '[]'
curl -s -o /dev/null -w "with-key: %{http_code}  (expect 2xx)\n" \
  -X POST https://<url>/v1/ingest -H 'content-type: application/json' \
  -H 'Authorization: Bearer <key>' -d '[]'
```

Then open the daemon URL in a browser for the dashboard.

<Note>
Current release: `v0.24.0`. Pin the image to a specific release or a minor channel (see [Auto-update](/deployment/auto-update)); never track `:latest` in production. The ingest key must not start with `vk_`. Only `/v1/ingest` and `/health` need to be public; the rest can be firewall-restricted to your internal network.
</Note>
