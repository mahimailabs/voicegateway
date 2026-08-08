# Deploy VoiceGateway to Railway with a coding agent

Copy the block below into Claude Code, Cursor, or any agent that can run shell
commands. It deploys the VoiceGateway collector to your own Railway account and
verifies it end to end.

Every command in it was run against a real Railway project, and every trap it
warns about is one that was actually hit. It is deliberately explicit about what
the agent must NOT do, because an agent with your Railway CLI session can reach
every project in every workspace you belong to.

---

## The prompt

````text
Deploy VoiceGateway (an open-source profiler for LiveKit and Pipecat voice
agents) to my Railway account, then verify it actually works. Follow these
steps exactly and stop at the first one that fails.

## Rules

- Create a NEW Railway project. Never deploy into, modify, or delete an
  existing project or service. List projects first and show me the list.
- Never print a password, a database URL containing one, or an API key. Write
  secrets to a file and tell me the path.
- If `railway whoami` fails, stop and tell me to run `railway login` myself.
  Do not attempt to log in.
- Show me the cost shape before creating anything: this creates one service
  plus a managed Postgres, both billed by usage.

## 1. Check the CLI

    railway --version
    railway whoami

## 2. Get the source

VoiceGateway is deployed from source, because Railway needs the repo's
`railway.json` to find the Dockerfile:

    git clone https://github.com/mahimailabs/voicegateway
    cd voicegateway

Confirm `railway.json` exists at the root and names
`src/voicegateway/Dockerfile`. If it does not, stop: without it Railway falls
back to Nixpacks and silently builds a DIFFERENT artifact that still appears to
deploy successfully.

## 3. Create the project

Non-interactive `railway init` requires a workspace when the account has more
than one. Get the id first, and show me the projects it lists:

    railway list --json

Take the `workspace.id` for the workspace I tell you to use, then:

    railway init --name voicegateway --workspace <WORKSPACE_ID> --json

## 4. Add Postgres and the app service

    railway add --database postgres
    railway add --service voicegateway

Then run `railway list --json` and confirm the project has exactly two
services: `Postgres` and `voicegateway`. Running `railway add --database`
twice creates a SECOND Postgres; if that happened, delete the extra with
`railway service delete --service <name>`.

## 5. Set the variables

Set them as Railway variable REFERENCES, not by copying the database URL.
Railway resolves these at deploy time, so the password never leaves Railway,
and there is nothing to hand-edit:

    railway variables --service voicegateway \
      --set 'VOICEGW_DB_URL=postgresql+asyncpg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.RAILWAY_PRIVATE_DOMAIN}}:5432/${{Postgres.PGDATABASE}}' \
      --set "VOICEGW_API_KEY=$(openssl rand -hex 32)"

The `postgresql+asyncpg://` scheme is required. Railway's own `DATABASE_URL` is
`postgresql://`, which fails at startup because it wants a driver the image does
not carry. Do not use `DATABASE_URL` directly.

Save the generated key somewhere I can find it, and tell me the path. Do not
print it.

## 6. Deploy and get a URL

    railway up --service voicegateway --ci
    railway domain --service voicegateway

Read the public hostname from `RAILWAY_PUBLIC_DOMAIN`:

    railway variables --service voicegateway --json

## 7. Verify, and do not skip this

Run all four. Report the actual status codes.

    # 1. Health. Expect 200 and {"status":"ok"}.
    curl -fsS https://<DOMAIN>/health

    # 2. Auth is enforced. Expect 401.
    curl -s -o /dev/null -w '%{http_code}\n' https://<DOMAIN>/v1/rooms/x/latency

    # 3. Postgres is connected. Expect 404, NOT 503.
    #    404 = the room is unknown, so storage answered.
    #    503 = storage is not configured, so step 5 did not take.
    curl -s -o /dev/null -w '%{http_code}\n' \
      -H "Authorization: Bearer <KEY>" https://<DOMAIN>/v1/rooms/x/latency

    # 4. A real write and read-back. Expect {"accepted":1} then a JSON body
    #    whose components.llm_ttft_ms is 447.0.
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

Step 4 is the one that matters: it proves a row was written to Postgres and
read back through the room correlation, which nothing before it proves.

## 8. Report

Tell me the public URL, where the API key is saved, the four status codes, and
what it costs to leave running. Then tell me how to tear it down:
`railway delete`.

## Known wart

`/health` will report `version: 0.5.0` on a source deploy. Railway passes no
VERSION build arg, so the Dockerfile's default shows. It does not indicate a
wrong build.
````

---

## Connecting an agent to it

Once deployed, point a VoiceGateway-instrumented agent at it:

```python
from voicegateway import attach

attach(
    session,
    collector_url="https://<your-domain>",
    api_key="<the key from step 5>",
)
```

Or set `VOICEGW_COLLECTOR_URL` and `VOICEGW_API_KEY` in the agent's
environment and call `attach(session)`.

## Why this is a prompt and not a script

A script would have to guess your workspace, your project naming, and what you
already have running. The steps that need judgement are exactly the ones worth
handing to an agent that can show you the list and ask. The parts that do not
need judgement are given as literal commands so the agent has nothing to invent.

## Verified against

A real deploy on Railway CLI 5.23.2, building `src/voicegateway/Dockerfile` via
`railway.json`, against Railway's managed Postgres. The verification in step 7
is the sequence that deploy actually returned.
