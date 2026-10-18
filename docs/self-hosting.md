# Self-hosting VoiceGateway

Run VoiceGateway on your own machine or server, connect your LiveKit project and see your agent's calls. This is the V1 preview: see [What is not there yet](#what-is-not-there-yet) before you start.

## What you need

- Docker with Compose v2 and at least 10 GB of free disk: the first build downloads Go and npm dependencies.
- `git` and `make`.
- A LiveKit project, on LiveKit Cloud or self-hosted: its URL, an API key and its secret.
- A voice agent built on LiveKit Agents for Python 1.8 or later.

## 1. Start the stack

```bash
git clone https://github.com/mahimailabs/voicegateway.git
cd voicegateway
make up
make preflight
```

`make up` builds the image and starts six services (web, worker, Postgres, ClickHouse, Redis and an S3 bucket). On the first run it writes `deploy/.env` with fresh random secrets; keep that file, and never commit it. `make preflight` should report OK for postgres, clickhouse, redis and s3.

The console and the API are both at [localhost:8080](http://localhost:8080). To use another port, set `VOICEGATEWAY_WEB_PORT` before `make up`.

## 2. Create the first admin

```bash
make user-add EMAIL=you@example.com
```

It asks for a password, or reads it from `VOICEGATEWAY_USER_PASSWORD`. Sign in at localhost:8080 with that email and password.

## 3. Add your LiveKit project

```bash
export LIVEKIT_URL=wss://your-project.livekit.cloud
export LIVEKIT_API_KEY=...
export LIVEKIT_API_SECRET=...
make project-add NAME="Production"
```

The secret is encrypted at rest and never shown again. The project then appears in the console's project switcher. VoiceGateway starts reading your media server, SIP, egress and ingress through the LiveKit API right away.

## 4. Point your agent at VoiceGateway

In the agent's environment, set:

```bash
LIVEKIT_OBSERVABILITY_URL=http://<voicegateway-host>:8080
```

and start the agent session with `record=True`. LiveKit Agents then uploads its traces, logs, metrics and session report to VoiceGateway, signed with the same LiveKit API key and secret. The agent's audio path is untouched: if VoiceGateway is down, calls carry on.

## 5. Send LiveKit's webhooks to VoiceGateway

In your LiveKit project's webhook settings, add `https://<voicegateway-host>/livekit/webhook`, signed with the API key from step 3.

Without the webhook, calls still appear, but they stay **open**, with no end time, duration or participants, because a call ends when LiveKit reports the room finished.

## Reaching VoiceGateway from LiveKit

The agent's uploads and LiveKit's webhooks must reach your VoiceGateway host:

- Agent on the same machine: `http://localhost:8080` is enough for the uploads.
- LiveKit Cloud webhooks, or agents elsewhere: VoiceGateway needs a public HTTPS address. Put it behind your reverse proxy, then set `VOICEGATEWAY_PUBLIC_URL=https://<host>` and `VOICEGATEWAY_TRUSTED_PROXIES=<proxy address or CIDR>` in `deploy/.env` and run `make up` again. For a quick test, `cloudflared tunnel --url http://localhost:8080` gives a temporary public URL.

## Stop, upgrade and reset

```bash
make down                 # stop; your data is kept
git pull && make up       # upgrade; migrations run on start
```

`docker compose -f deploy/compose.yaml --env-file deploy/.env down --volumes` deletes all stored data.

## What is not there yet

- **No invitations yet.** Add teammates from the command line with `make user-add EMAIL=... ROLE=member` (or `viewer`, or `admin`).
- **No retention limit yet.** Raw call data is kept until you delete it.
- **Some panels are not served yet**, such as project settings and provider probes: they say "Not served by this server yet".
- **Notifications** for alerts are not delivered yet.

Found something broken? [Open an issue](https://github.com/mahimailabs/voicegateway/issues/new/choose).
