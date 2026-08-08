---
title: "Deploy to Fly.io"
description: "Run the VoiceGateway daemon on Fly.io with managed Postgres, automatic HTTPS, and multi-region placement."
---
Low ops. Automatic HTTPS. Deploy in multiple regions to sit near your agents.

<Note>
Everything specific to VoiceGateway on this page is verified against this repository.
Everything specific to Fly (flyctl flags, `fly.toml` fields, managed Postgres, generated
hostnames) is written from Fly's documented behavior and is not verified here. Fly changes
on its own schedule; check their docs if a step does not match what you see.
</Note>

<Warning>
Use `0.24.0` or newer. Images at or below `0.22.3` require a config file at
`VOICEGW_CONFIG` and exit at boot without one, raising
`ConfigError: Config file not found: /data/voicegw.yaml` before the port binds, so Fly
restarts the machine forever.
</Warning>

<Note>
**Provide a config file anyway.** From `0.24.0` a missing one is only a warning and the
daemon boots on built-in defaults, but a default-config daemon has no providers, models,
or projects declared. The image bakes `VOICEGW_CONFIG=/data/voicegw.yaml`; write or mount
a `voicegw.yaml` there with a `[[files]]` block in `fly.toml` or a volume, and repoint
`VOICEGW_CONFIG` if you use a different path.
</Note>

<Tip>
Deploy in a region close to where your agents run to cut ingest latency: `--region` on `fly deploy`, or `primary_region` in `fly.toml`.
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
  image = "mahimairaja/voicegateway:0.24.0"

[http_service]
  internal_port = 8080  # optional: the image binds $PORT when the platform sets one
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

`fly postgres attach` sets `DATABASE_URL` automatically, in `postgres://...` form.

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
