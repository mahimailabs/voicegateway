---
title: "Deploy to Railway"
description: "Run the VoiceGateway daemon on Railway with managed Postgres and automatic HTTPS, deploying from the repository."
---
Lowest ops: Railway handles managed Postgres, TLS, and a public URL automatically.

<Warning>
**This path needs a release newer than v0.22.3.** The published image at or below
`0.22.3` requires a config file at `VOICEGW_CONFIG` and exits at boot without one, so a
reader deploying that image gets a restart loop. Deploying from the repository, as below,
builds current code and works today.
</Warning>

<Tip>
This is the least-ops path. Cost is usage-based and higher than a self-managed VPS.
</Tip>

## Prerequisites

- A [Railway](https://railway.com) account

<Steps>

### Create the service

In your Railway project, choose **New**, then **Deploy from GitHub repo**, and pick the
VoiceGateway repository. No build configuration is needed.

`railway.json` at the repository root supplies it: the Dockerfile builder, the path
`src/voicegateway/Dockerfile`, and `healthcheckPath: /health`.

<Note>
That file matters more than it looks. Railway looks for a Dockerfile at the repository
root, and VoiceGateway's lives under `src/voicegateway/`. Without `railway.json`, Railway
falls back to Nixpacks and builds a different artifact, and the deploy still reports
success.
</Note>

The exposed port is optional. The image binds `$PORT` when the platform sets one, which
Railway does, and the container healthcheck resolves the same way. `VOICEGW_PORT`
overrides both; the default is `8080`.

### Add Postgres

Choose **New** in the same project and add the **PostgreSQL** plugin.

### Configure environment variables

In the app service's **Variables** tab, add:

| Variable | Value |
|---|---|
| `VOICEGW_DB_URL` | `postgresql+asyncpg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.RAILWAY_PRIVATE_DOMAIN}}:5432/${{Postgres.PGDATABASE}}` |
| `VOICEGW_API_KEY` | your ingest key (`openssl rand -hex 32`; must not start with `vk_`) |

Paste the `VOICEGW_DB_URL` value literally. Railway resolves the `${{Postgres.*}}`
references at deploy time, so there is nothing to copy by hand and the password never
leaves Railway.

<Warning>
The `+asyncpg` is the part that matters. Railway hands out a `postgresql://` URL, and
VoiceGateway reads only `VOICEGW_DB_URL` and does not normalise the scheme. A plain
`postgresql://` fails because it wants `psycopg2`, which the image does not carry.
</Warning>

`VOICEGW_API_KEY` registers a wildcard ingest key without needing an `auth.api_keys:`
block in `voicegw.yaml`.

### Deploy

Redeploy after setting variables. HTTPS is automatic at
`https://<your-service>.<your-project>.up.railway.app`; attach a custom domain in the
service's **Settings** tab if you want one.

</Steps>

## Verify

Follow the steps at [Verify](/deployment/index#verify), using your Railway service URL as
the daemon URL and `VOICEGW_API_KEY` as the key.

A working deploy answers `/health` with `200`, and answers an authenticated
`/v1/rooms/<room>/latency` with `404` rather than `503`. The `404` is the useful signal:
it means Postgres connected and migrations ran, and that room simply has no data yet.

<Note>
`/health` reports version `0.5.0` on a source deploy. Railway passes no `VERSION` build
argument and the Dockerfile's `ARG` defaults to that. The build is current; only the
reported string is wrong.
</Note>

## Connect your agent

See [Connect your agent](/deployment/index#connect-your-agent). Use the Railway URL as
`collector_url` and `VOICEGW_API_KEY` as `api_key`.
