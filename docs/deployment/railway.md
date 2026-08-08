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

## Prerequisites

- A [Railway](https://railway.com) account

<Steps>
  <Step title="Create the service">
    Choose **New**, then **Docker Image**, and enter `mahimairaja/voicegateway:0.24.0`.

    The exposed port is optional. The image binds `$PORT` when the platform sets one, which
    Railway does, and the container healthcheck resolves the same way. `VOICEGW_PORT`
    overrides both; the default is `8080`.
  </Step>
  <Step title="Add Postgres">
    Choose **New** in the same project and add the **PostgreSQL** plugin. Railway provisions a
    managed instance and exposes its connection details to the other services in the project.
  </Step>
  <Step title="Configure environment variables">
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
  </Step>
  <Step title="Deploy">
    Redeploy after setting variables. HTTPS is automatic at
    `https://<your-service>.<your-project>.up.railway.app`; attach a custom domain in the
    service's **Settings** tab if you want one.
  </Step>
</Steps>

## Verify

Four checks, in order. Each rules out a different failure, so run them all.

<Steps>
  <Step title="The process is alive">
    ```bash
    curl -fsS https://<your-domain>/health
    ```

    Returns `200` and `{"status":"ok"}`.
  </Step>
  <Step title="Authentication is enforced">
    ```bash
    curl -s -o /dev/null -w '%{http_code}\n' https://<your-domain>/v1/rooms/x/latency
    ```

    Returns `401`. Anything else means `VOICEGW_API_KEY` did not register.
  </Step>
  <Step title="Postgres is connected">
    ```bash
    curl -s -o /dev/null -w '%{http_code}\n' \
      -H "Authorization: Bearer <your-key>" https://<your-domain>/v1/rooms/x/latency
    ```

    Returns **`404`, not `503`**. This is the one worth reading carefully. `404` means storage
    answered and that room simply has no data, so Postgres connected and migrations ran.
    `503` means `VOICEGW_DB_URL` never took.
  </Step>
  <Step title="A row survives a round trip">
    ```bash
    curl -s -X POST -H "Authorization: Bearer <your-key>" \
      -H 'Content-Type: application/json' \
      -d '[{"id":"verify-1","timestamp":'"$(date +%s)"',"project":"verify",
           "modality":"llm","model_id":"openai/gpt-4o-mini","provider":"openai",
           "input_units":100,"output_units":50,"cost_usd":0.00042,
           "pricing_source":"test","ttfb_ms":447.0,"status":"success",
           "session_id":"verify-1","metadata":{"room":"verify-room"}}]' \
      https://<your-domain>/v1/ingest

    curl -s -H "Authorization: Bearer <your-key>" \
      https://<your-domain>/v1/rooms/verify-room/latency
    ```

    Returns `{"accepted":1}`, then a body whose `components.llm_ttft_ms` is `447.0`. This is
    the only check that proves persistence rather than that the process is merely running.
  </Step>
</Steps>

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

## Or let a coding agent do it

<Prompt
  description="Runs every step on this page against the Railway CLI, including the four checks."
  icon="rocket"
  actions={["copy"]}
>
Deploy VoiceGateway to my Railway account and verify it works. VoiceGateway is an open-source profiler for LiveKit and Pipecat voice agents. Stop at the first step that fails and tell me what happened.

Rules. Create a NEW Railway project: never deploy into, modify, or delete a project or service that already exists, and run `railway list` first and show me the output. Never print an API key or any URL containing a password; write secrets to a file and tell me the path. If `railway whoami` fails, stop and tell me to run `railway login` myself. Before creating anything, tell me the cost shape: one service plus a managed Postgres, both billed by usage.

Step 1. Run `railway --version` and `railway whoami`.

Step 2. Create the project. `railway init` needs a workspace when the account has more than one, and wants the workspace ID rather than its display name. Run `railway list --json`, show me the projects, then run `railway init --name voicegateway --workspace WORKSPACE_ID --json`.

Step 3. Run `railway add --database postgres`, then `railway add --service voicegateway --image mahimairaja/voicegateway:0.24.0`. Run `railway list --json` and confirm exactly two services exist, Postgres and voicegateway. Running the database command twice silently creates a second Postgres; delete any extra with `railway service delete --service NAME --yes`.

Step 4. Set the variables on the voicegateway service with `railway variables`. Set VOICEGW_API_KEY to the output of `openssl rand -hex 32`, save it to a file, tell me the path, and do not print it. Set VOICEGW_DB_URL using Railway's own variable-reference syntax pointing at the Postgres service, so nothing is hand-copied and the password never leaves Railway: the scheme is postgresql+asyncpg, the user and password come from the Postgres service's PGUSER and PGPASSWORD, the host from its RAILWAY_PRIVATE_DOMAIN, the port is 5432, and the database from its PGDATABASE. The +asyncpg is required: Railway's own DATABASE_URL is postgresql://, which fails at boot because it wants a driver the image does not carry, so do not use DATABASE_URL directly.

Step 5. Run `railway redeploy --service voicegateway --yes`, then `railway domain --service voicegateway`. Read the public hostname from RAILWAY_PUBLIC_DOMAIN via `railway variables --service voicegateway --json`.

Step 6. Verify. Run all four and report the actual status codes. (a) GET /health expects 200 and a body with status ok. (b) GET /v1/rooms/x/latency with no Authorization header expects 401. (c) GET /v1/rooms/x/latency with the bearer key expects 404 and NOT 503: 404 means Postgres connected and migrations ran and that room has no data, while 503 means step 4 did not take. (d) POST one row to /v1/ingest as a JSON array holding a single object with id verify-1, timestamp as the current unix time integer, project verify, modality llm, model_id openai/gpt-4o-mini, provider openai, input_units 100, output_units 50, cost_usd 0.00042, pricing_source test, ttfb_ms 447.0, status success, session_id verify-1, and metadata holding room set to verify-room. Expect a response of accepted 1. Then GET /v1/rooms/verify-room/latency and expect a body whose components.llm_ttft_ms is 447.0. This is the only check that proves persistence rather than that the process is merely running.

Step 7. Report the public URL, where the key is saved, the four status codes, what it costs to leave running, and that `railway delete` tears it down.
</Prompt>
