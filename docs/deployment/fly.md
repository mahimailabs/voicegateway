---
title: "Deploy to Fly.io"
description: "Run the VoiceGateway daemon on Fly.io with managed Postgres, automatic HTTPS, and multi-region placement."
---
Low ops. Automatic HTTPS. Deploy in multiple regions to sit near your agents.

<Tip>
Deploy in a region close to where your agents run to cut ingest latency. Fly lets you place machines in specific regions with `--region` on `fly deploy` or via the `primary_region` key in `fly.toml`.
</Tip>

## Prerequisites

- [`flyctl`](https://fly.io/docs/hands-on/install-flyctl/) installed
- A Fly account: `fly auth login`

<Steps>

### Create fly.toml

Create `fly.toml` in a working directory:

```toml
app = "<your-app-name>"

[build]
  image = "mahimairaja/voicegateway:0.9.2"

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = false
  min_machines_running = 1
```

### Add Postgres

**Option A: Fly Postgres (unmanaged)**

```bash
fly postgres create --name <pg-app-name>
fly postgres attach <pg-app-name> --app <your-app-name>
```

`fly postgres attach` sets `DATABASE_URL` on your app automatically in `postgres://...` form.

**Option B: Neon or another managed provider**

Skip `fly postgres create` and supply the connection string directly in the next step.

### Set secrets

<Warning>
Fly's `DATABASE_URL` (from `fly postgres attach`) uses the `postgres://` scheme. VoiceGateway requires `postgresql+asyncpg://`. Copy the connection string and rewrite the scheme; everything after `://` stays the same.
</Warning>

```bash
fly secrets set \
  VOICEGW_DB_URL="postgresql+asyncpg://<user>:<pass>@<host>:<port>/<db>" \
  VOICEGW_API_KEY="<your-ingest-key>"
```

`VOICEGW_API_KEY` must not start with `vk_`. Generate it with `openssl rand -hex 32`. No volume is needed since data is stored in Postgres.

### Deploy

```bash
fly deploy
```

HTTPS is automatic at `https://<your-app-name>.fly.dev`.

</Steps>

## Verify

Follow the steps at [Verify](/deployment/index#verify), using `https://<your-app-name>.fly.dev` as the daemon URL and `VOICEGW_API_KEY` as the key.

## Connect your agent

See [Connect your agent](/deployment/index#connect-your-agent). Use `https://<your-app-name>.fly.dev` as `collector_url` and `VOICEGW_API_KEY` as `api_key`.
