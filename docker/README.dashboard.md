# mahimairaja/voicegateway-dashboard

Web dashboard for [VoiceGateway](https://hub.docker.com/r/mahimairaja/voicegateway) -- self-hosted inference gateway for voice AI.

Full documentation: **[docs.voicegateway.dev](https://docs.voicegateway.dev)**

---

## Quick Start

Run alongside the main `voicegateway` container:

```bash
docker run -d \
  --name voicegateway-dashboard \
  --link voicegateway:voicegateway \
  -p 9090:9090 \
  mahimairaja/voicegateway-dashboard:latest
```

Visit `http://localhost:9090`.

---

## With Docker Compose

See the [voicegateway image docs](https://hub.docker.com/r/mahimairaja/voicegateway) for the recommended `docker-compose.yml`.

---

## Features

- **Overview** -- traffic, cost, and latency summary
- **Settings** -- add/edit providers, register models via web UI
- **Projects** -- full CRUD with budget gauges and cost charts
- **Costs / Latency / Logs** -- per-project, per-provider, per-model drill-down
- **Audit log** -- every config change tracked

API keys added via dashboard are encrypted with Fernet before storage.

---

## Tags

- `latest` -- most recent release
- `{version}` -- matches the voicegateway image version

## Architectures

- `linux/amd64`
- `linux/arm64`

## Ports

- `9090` -- web UI

## Source

[github.com/mahimailabs/voicegateway](https://github.com/mahimailabs/voicegateway) -- MIT License
