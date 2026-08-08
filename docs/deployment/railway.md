---
title: "Deploy to Railway"
description: "Run the VoiceGateway daemon on Railway with managed Postgres and automatic HTTPS, from the published image."
---
Lowest ops: Railway handles managed Postgres, TLS, and a public URL automatically.

<Warning>
Use `0.24.0` or newer. Images at or below `0.22.3` require a config file at
`VOICEGW_CONFIG` and exit at boot without one, so they restart-loop on Railway with
`ConfigError: Config file not found: /data/voicegw.yaml`. From `0.24.0` the daemon starts
without a config file, binds the port Railway assigns, and needs no build configuration.
</Warning>

<Tip>
This is the least-ops path. Cost is usage-based and higher than a self-managed VPS.
</Tip>

## Deploy it with your coding agent

Paste this into Claude Code, Cursor, or any agent that can run shell commands. It creates
the project, provisions Postgres, deploys, and verifies the result.

```text Railway deploy prompt
Deploy VoiceGateway to my Railway account and verify it works. VoiceGateway is an
open-source profiler for LiveKit and Pipecat voice agents. Stop at the first step
that fails and tell me what happened.

RULES
- Create a NEW Railway project. Never deploy into, modify, or delete a project or
  service that already exists. Run `railway list` first and show me the output.
- Never print an API key or any URL containing a password. Write secrets to a file
  and tell me the path.
- If `railway whoami` fails, stop and tell me to run `railway login` myself.
- Tell me the cost shape before creating anything: one service plus a managed
  Postgres, both billed by usage.

1. CHECK THE CLI
   railway --version
   railway whoami

2. CREATE THE PROJECT
   `railway init` needs a workspace when the account has more than one, and wants
   the workspace ID, not its display name. Get it, show me the projects, then create:
       railway list --json
       railway init --name voicegateway --workspace <WORKSPACE_ID> --json

3. ADD POSTGRES AND THE APP
       railway add --database postgres
       railway add --service voicegateway --image mahimairaja/voicegateway:0.24.0
   Then run `railway list --json` and confirm the project has exactly two services,
   Postgres and voicegateway. Running `railway add --database` twice silently
   creates a second Postgres; delete any extra with
   `railway service delete --service <name> --yes`.

4. SET THE VARIABLES
   Set the database URL as Railway variable REFERENCES, not by copying the URL.
   Railway resolves them at deploy time, so nothing is hand-edited and the password
   never leaves Railway:
       railway variables --service voicegateway \
         --set 'VOICEGW_DB_URL=postgresql+asyncpg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.RAILWAY_PRIVATE_DOMAIN}}:5432/${{Postgres.PGDATABASE}}' \
         --set "VOICEGW_API_KEY=$(openssl rand -hex 32)"
   The +asyncpg is required. Railway's own DATABASE_URL is postgresql://, which
   fails at boot because it wants a driver the image does not carry. Do not use
   DATABASE_URL directly.
   Save the generated key to a file and tell me the path. Do not print it.

5. DEPLOY AND GET A URL
       railway redeploy --service voicegateway --yes
       railway domain --service voicegateway
   Read the hostname from RAILWAY_PUBLIC_DOMAIN:
       railway variables --service voicegateway --json

6. VERIFY. Run all four and report the actual status codes.
   a. curl -fsS https://<DOMAIN>/health
      Expect 200 and {"status":"ok"}.
   b. curl -s -o /dev/null -w '%{http_code}\n' https://<DOMAIN>/v1/rooms/x/latency
      Expect 401. Auth is enforced.
   c. curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer <KEY>" \
        https://<DOMAIN>/v1/rooms/x/latency
      Expect 404, NOT 503. 404 means Postgres connected and migrations ran, and
      that room simply has no data. 503 means step 4 did not take.
   d. Write a row, then read it back:
        curl -s -X POST -H "Authorization: Bearer <KEY>" \
          -H 'Content-Type: application/json' \
          -d '[{"id":"verify-1","timestamp":'"$(date +%s)"',"project":"verify",
               "modality":"llm","model_id":"openai/gpt-4o-mini","provider":"openai",
               "input_units":100,"output_units":50,"cost_usd":0.00042,
               "pricing_source":"test","ttfb_ms":447.0,"status":"success",
               "session_id":"verify-1","metadata":{"room":"verify-room"}}]' \
          https://<DOMAIN>/v1/ingest
        curl -s -H "Authorization: Bearer <KEY>" \
          https://<DOMAIN>/v1/rooms/verify-room/latency
      Expect {"accepted":1} then a body whose components.llm_ttft_ms is 447.0.
      This is the only step that proves Postgres persistence rather than that the
      process is merely alive.

7. REPORT the public URL, where the key is saved, the four status codes, the cost
   of leaving it running, and that `railway delete` tears it down.
```

Everything above was run against a real Railway project. Step 6d returns exactly what it
says it will.

## Or click through the dashboard

<Steps>

### Create the service

Choose **New**, then **Docker Image**, and enter `mahimairaja/voicegateway:0.24.0`.

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

## Connect your agent

See [Connect your agent](/deployment/index#connect-your-agent). Use the Railway URL as
`collector_url` and `VOICEGW_API_KEY` as `api_key`.

## Deploying from source instead

Pointing Railway at a clone or fork works too, and needs no build configuration:
`railway.json` at the repository root supplies the Dockerfile builder, the path
`src/voicegateway/Dockerfile`, and `healthcheckPath: /health`.

<Note>
A source deploy reports version `0.5.0` at `/health`. Railway passes no `VERSION` build
argument, so the Dockerfile's `ARG` default shows. The build is current; only the reported
string is wrong. Image deploys report their real version.
</Note>
