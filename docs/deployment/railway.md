---
title: "Deploy to Railway"
description: "Run the VoiceGateway daemon on Railway with managed Postgres and automatic HTTPS."
---
Lowest ops: Railway handles managed Postgres, TLS, and a public URL automatically.

<Note>
Everything specific to VoiceGateway on this page is verified against this repository.
Everything specific to Railway (the dashboard flow, managed Postgres, volumes, generated
hostnames) is written from Railway's documented behavior and is not verified here. Their
UI changes; check their docs if a step does not match what you see.
</Note>

<Warning>
**Boot requirement.** The image bakes `VOICEGW_CONFIG=/data/voicegw.yaml`. The daemon loads that file unconditionally at startup, before it binds a port. If nothing exists at that path it raises `ConfigError` and exits inside `main()`: `/health` never comes up, and Railway restarts the container forever. Env vars alone (including `VOICEGW_API_KEY`) do not satisfy this: you need an actual file at `VOICEGW_CONFIG`. Provide a `voicegw.yaml` and point `VOICEGW_CONFIG` at it, the same way this repo's `docker-compose.yml` and `docker-compose.collector.yml` do (mount the file, then set `VOICEGW_CONFIG` to its in-container path).

The documented Railway mechanism for getting that file onto the container is a Railway volume, mounted at a fixed path and populated with the file (for example via a release command, or a custom image build that copies it in). This has not been confirmed against a live Railway deploy in this pass: verify it on your first deploy, and check the deploy logs for the `ConfigError` line if the service still restart-loops.
</Warning>

<Tip>
This is the least-ops path. Cost is usage-based and higher than a self-managed VPS.
</Tip>

## Prerequisites

- A [Railway](https://railway.com) account

<Steps>

### Create the service

1. In your Railway project, click **New** and choose **Docker Image**.
2. Enter `mahimairaja/voicegateway:0.22.3` as the image.
3. In the service settings, set the **exposed port** (also shown as "Port" or "Target Port") to `8080`.

### Add Postgres

Click **New** in the same project and add the **PostgreSQL** plugin. Railway provisions managed Postgres and injects `DATABASE_URL` (in `postgres://...` form) into services in the project.

### Configure environment variables

In the collector service's **Variables** tab, add:

<Warning>
Railway's `DATABASE_URL` uses the `postgres://` scheme. VoiceGateway requires `postgresql+asyncpg://`. Copy the value Railway provides and rewrite the scheme prefix; everything after `://` stays the same.

Example shape:
```
VOICEGW_DB_URL=postgresql+asyncpg://<user>:<pass>@<host>:<port>/<db>
```
</Warning>

| Variable | Value |
|---|---|
| `VOICEGW_DB_URL` | Railway's Postgres URL with scheme rewritten to `postgresql+asyncpg://` |
| `VOICEGW_API_KEY` | `<your-ingest-key>` (generate with `openssl rand -hex 32`; must not start with `vk_`) |

`VOICEGW_API_KEY` registers a wildcard ingest key without an `auth.api_keys:` block in `voicegw.yaml`. It does not remove the requirement for the file itself: see the boot requirement above.

### Deploy

Redeploy after setting variables. HTTPS is automatic at `https://<your-service>.<your-project>.up.railway.app`; attach a custom domain in the service's **Settings** tab if you want one.

</Steps>

## Verify

Follow the steps at [Verify](/deployment/index#verify), using your Railway service URL as the daemon URL and `VOICEGW_API_KEY` as the key.

## Connect your agent

See [Connect your agent](/deployment/index#connect-your-agent). Use the Railway URL as `collector_url` and `VOICEGW_API_KEY` as `api_key`.
