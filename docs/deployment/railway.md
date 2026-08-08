---
title: "Deploy to Railway"
description: "Run the VoiceGateway daemon on Railway with managed Postgres and automatic HTTPS, from the published image."
---
Lowest ops: Railway handles managed Postgres, TLS, and a public URL automatically. Cost is
usage-based and higher than a self-managed VPS.

<Warning>
Use `0.24.0` or newer.
</Warning>

<Steps>
  <Step title="Create the service">
    Choose **New**, then **Docker Image**, and enter `mahimairaja/voicegateway:0.24.0`.

    The exposed port is optional: the image binds `$PORT`, which Railway sets, and the
    healthcheck follows it. `VOICEGW_PORT` overrides both.
  </Step>
  <Step title="Add Postgres">
    Choose **New** in the same project and add the **PostgreSQL** plugin.
  </Step>
  <Step title="Configure environment variables">
    In the app service's **Variables** tab, add:

    | Variable | Value |
    |---|---|
    | `VOICEGW_DB_URL` | `postgresql+asyncpg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.RAILWAY_PRIVATE_DOMAIN}}:5432/${{Postgres.PGDATABASE}}` |
    | `VOICEGW_API_KEY` | your ingest key (`openssl rand -hex 32`; must not start with `vk_`) |

    Paste `VOICEGW_DB_URL` literally: Railway resolves the `${{Postgres.*}}` references at
    deploy time, so the password never leaves Railway.

    <Warning>
    Keep the `+asyncpg`, and do not substitute Railway's own `DATABASE_URL`. It is plain
    `postgresql://`, which fails at boot: the image carries no driver for it.
    </Warning>

    `VOICEGW_API_KEY` registers a wildcard ingest key without needing an `auth.api_keys:`
    block in `voicegw.yaml`.
  </Step>
  <Step title="Deploy">
    Redeploy after setting variables. HTTPS is automatic at
    `https://<your-service>.<your-project>.up.railway.app`.
  </Step>
</Steps>

## Verify

Two checks. The first proves the deploy works, the second proves it is not open to the
internet. Run both.

<Steps>
  <Step title="A row survives a round trip">
    ```bash
    curl -s -X POST -H "Authorization: Bearer <your-key>" \
      -H 'Content-Type: application/json' \
      -d '[{"id":"verify-1","timestamp":'"$(date +%s)"',"project":"verify",
           "modality":"llm","model_id":"openai/gpt-4o-mini","provider":"openai",
           "input_units":100,"output_units":50,
           "pricing_source":"test","ttfb_ms":447.0,"status":"success",
           "session_id":"verify-1","metadata":{"room":"verify-room"}}]' \
      https://<your-domain>/v1/ingest

    curl -s -H "Authorization: Bearer <your-key>" \
      https://<your-domain>/v1/rooms/verify-room/latency
    ```

    Returns `{"accepted":1}`, then a body whose `components.llm_ttft_ms` is `447.0`.

    This is the check that matters. Passing it proves the process runs, your key is
    accepted, Postgres connected, migrations ran, and a write survives a read. Nothing
    else needs to pass for the deploy to be good.
  </Step>
  <Step title="The endpoint is not open to the internet">
    ```bash
    curl -s -o /dev/null -w '%{http_code}\n' https://<your-domain>/v1/rooms/x/latency
    ```

    Returns `401`. The check above proves your key is accepted; this one proves a request
    *without* it is refused. `/v1/ingest` has to be publicly reachable for agents to push
    to it, so anything other than `401` means anyone can write to your telemetry.
  </Step>
</Steps>

### If the round trip failed

Two commands tell you where.

```bash
curl -fsS https://<your-domain>/health

curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer <your-key>" https://<your-domain>/v1/rooms/x/latency
```

| Result | Meaning |
|---|---|
| `/health` is not `200` | The process is not up. Railway gates its own healthcheck on this path, so the deploy log is the place to look, not the network. |
| `503` | `VOICEGW_DB_URL` never took. Re-read step 3, including the `+asyncpg`. |
| `401` | The key you are sending is not the one in `VOICEGW_API_KEY`. |
| `404` | Storage answered and that room has no data, so Postgres is fine and migrations ran. The failure is in the POST, not the deploy. |

<Note>
The round trip leaves one row behind, under project `verify`. It carries no cost, so it
does not move your totals, but it does count as one request. No API call removes it:
`DELETE /v1/projects/verify` deletes a project's configuration, not its recorded
requests. Run the check before you point real agents at the deploy and it stays easy to
ignore.
</Note>

To point an agent at it, use the Railway URL as `collector_url` and `VOICEGW_API_KEY` as
`api_key`. See [Connect your agent](/deployment/index#connect-your-agent).

## Deploying from source instead

A clone or fork works too, with no build configuration: `railway.json` at the repository
root supplies the Dockerfile builder, its path, and `healthcheckPath`. Such a build
reports `0.0.0.dev0` at `/health`, because Railway passes no `VERSION` argument. It is
current, just unstamped.

## Or let a coding agent do it

<Prompt
  description="Runs every step on this page against the Railway CLI, including both checks."
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

Step 6. Verify with two checks, and report the actual status codes for both. First, the round trip: POST one row to /v1/ingest as a JSON array holding a single object with id verify-1, timestamp as the current unix time integer, project verify, modality llm, model_id openai/gpt-4o-mini, provider openai, input_units 100, output_units 50, pricing_source test, ttfb_ms 447.0, status success, session_id verify-1, and metadata holding room set to verify-room. Do NOT send cost_usd: it defaults to zero, which keeps this verification row from moving my cost totals. Expect a response of accepted 1, then GET /v1/rooms/verify-room/latency and expect a body whose components.llm_ttft_ms is 447.0. That one check proves the process runs, the key is accepted, Postgres connected, migrations ran, and a write survives a read. Second, GET /v1/rooms/x/latency with NO Authorization header and expect 401, which proves the ingest surface is not open to the internet. If and only if the round trip fails, localize it: GET /health expects 200, and GET /v1/rooms/x/latency with the bearer key returns 503 when VOICEGW_DB_URL never took, 401 when the key is wrong, and 404 when Postgres is fine and the failure is in the POST.

Step 7. Report the public URL, where the key is saved, the status codes from step 6, what it costs to leave running, and that `railway delete` tears it down.
</Prompt>
