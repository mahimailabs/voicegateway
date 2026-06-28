---
title: "Deploy to Railway"
description: "Run the VoiceGateway fleet collector on Railway with managed Postgres and automatic HTTPS."
---

# Deploy to Railway

Lowest ops: Railway handles managed Postgres, TLS, and a public URL automatically.

<Tip>
This is the least-ops path. Cost is usage-based and higher than a self-managed VPS.
</Tip>

## Prerequisites

- A [Railway](https://railway.com) account

## Create the service

1. In your Railway project, click **New** and choose **Docker Image**.
2. Enter `mahimairaja/voicegateway:0.9.2` as the image.
3. In the service settings, set the **exposed port** (also shown as "Port" or "Target Port") to `8080`.

## Add Postgres

Click **New** in the same project and add the **PostgreSQL** plugin. Railway provisions a managed Postgres instance and injects a `DATABASE_URL` environment variable (in `postgres://...` form) into services in the project.

## Configure

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

`VOICEGW_API_KEY` registers a wildcard ingest key without needing a mounted `voicegw.yaml`.

## Deploy

Redeploy the service after setting variables. Railway builds and starts the container; HTTPS is automatic at `https://<your-service>.<your-project>.up.railway.app`. You can also attach a custom domain in the service's **Settings** tab.

## Verify

Follow the steps at [Verify](/deployment#verify), using your Railway service URL as the collector URL and `VOICEGW_API_KEY` as the key.

## Connect your agent

See [Connect your agent](/deployment#connect-your-agent). Use the Railway URL as `collector_url` and `VOICEGW_API_KEY` as `api_key`.
