# Self-hosting VoiceGateway

Run VoiceGateway on your own machine or server, connect your LiveKit project and see your agent's calls. This is the V1 preview: see [What is not there yet](#what-is-not-there-yet) before you start.

## What you need

- Docker with Compose v2 and at least 10 GB of free disk: the first build downloads Go and npm dependencies.
- `git` and `make`.
- A LiveKit project, on LiveKit Cloud or self-hosted: its URL, an API key and its secret.
- A voice agent built on LiveKit Agents for Python 1.8 or later.

## 1. Start the stack

```bash
git clone --branch v1.0.0-preview.3 --depth 1 https://github.com/mahimailabs/voicegateway.git
cd voicegateway
VOICEGATEWAY_IMAGE=ghcr.io/mahimailabs/voicegateway:1.0.0-preview.3 make up
make preflight
```

Or let `curl -fsSL https://voicegateway.dev/install.sh | bash` run these four commands for you: it checks for Docker, git and make first, and running it again in the same place updates the checkout. Set `VOICEGATEWAY_VERSION` or `VOICEGATEWAY_DIR` to change the release or the folder.

`make up` pulls the published image (amd64 or arm64) and keeps its name in `deploy/.env`, so later commands and new terminals use it too. Without `VOICEGATEWAY_IMAGE`, `make up` builds the image from this checkout, which needs about 10 GB free. Either way it starts six services (web, worker, Postgres, ClickHouse, Redis and an S3 bucket). On the first run it writes `deploy/.env` with fresh random secrets; keep that file, and never commit it. `make preflight` should report OK for postgres, clickhouse, redis and s3.

The console and the API are both at [localhost:8080](http://localhost:8080). To use another port, set `VOICEGATEWAY_WEB_PORT` before `make up`.

## 2. Create the first admin

```bash
make user-add EMAIL=you@example.com
```

It asks for a password, or reads it from `VOICEGATEWAY_USER_PASSWORD`. Sign in at localhost:8080 with that email and password.

Sign-in over plain HTTP works only from the machine VoiceGateway runs on. On a server, either forward the port with `ssh -L 8080:localhost:8080 <server>` and open localhost:8080 on your own machine, or serve VoiceGateway over HTTPS as described in [Reaching VoiceGateway from LiveKit](#reaching-voicegateway-from-livekit).

## 3. Add your LiveKit project

```bash
export LIVEKIT_URL=wss://your-project.livekit.cloud
export LIVEKIT_API_KEY=...
export LIVEKIT_API_SECRET=...
make project-add NAME="Production"
```

Before storing anything, VoiceGateway lists your LiveKit rooms once with that key and secret, so a wrong secret or an unreachable URL is refused right away; if your LiveKit server is not reachable from VoiceGateway yet, add `SKIP_VERIFY=1`. The secret is encrypted at rest and never shown again. The project then appears in the console's project switcher. VoiceGateway starts reading your media server, SIP, egress and ingress through the LiveKit API right away.

## 4. Point your agent at VoiceGateway

In the agent's environment, set:

```bash
LIVEKIT_OBSERVABILITY_URL=http://<voicegateway-host>:8080/ingest/<organisation-id>/<project-id>
```

and start the agent session with `record=True`. LiveKit Agents then uploads its traces, logs, metrics and session report to VoiceGateway, signed with the same LiveKit API key and secret. The agent's audio path is untouched: if VoiceGateway is down, calls carry on.

This setting sends the uploads to VoiceGateway instead of LiveKit Cloud's own observability, so those sessions no longer appear in LiveKit Cloud's dashboard.

## 5. Send LiveKit's webhooks to VoiceGateway

In your LiveKit project's webhook settings, add `https://<voicegateway-host>/ingest/<organisation-id>/<project-id>/livekit/webhook`, signed with the API key from step 3. Use the organisation and project IDs shown in the console URL. These IDs select the destination; LiveKit signatures still authenticate every request.

Without the webhook, calls still appear, but they have no start or observed end time, duration or participants, because those come from LiveKit's room events. They stay **open** until two hours pass with no activity. VoiceGateway then closes them as inferred, with the last reported activity as the end time when there is one.

## Reaching VoiceGateway from LiveKit

The agent's uploads and LiveKit's webhooks must reach your VoiceGateway host:

- Agent on the same machine: `http://localhost:8080` is enough for the uploads.
- LiveKit Cloud webhooks, or agents elsewhere: VoiceGateway needs a public HTTPS address. Put it behind your reverse proxy, then set `VOICEGATEWAY_PUBLIC_URL=https://<host>` and `VOICEGATEWAY_TRUSTED_PROXIES=<proxy address or CIDR>` in `deploy/.env` and run `make up` again. For a quick test, `cloudflared tunnel --url http://localhost:8080` gives a temporary public URL.

## Invite your team

In the console, open Settings, then People, and invite by email with a role. Without email set up, Settings shows each invitation or password reset link once, to copy and send yourself.

To have VoiceGateway email the links, set these in `deploy/.env` and run `make up` again:

```bash
VOICEGATEWAY_PUBLIC_URL=https://<host>                  # the base of every emailed link; required with SMTP
VOICEGATEWAY_SMTP_URL=smtps://user:pass@smtp.example.com:465   # or smtp://...:587 with STARTTLS
VOICEGATEWAY_SMTP_FROM=VoiceGateway <noreply@example.com>
```

VoiceGateway only sends over TLS. If your mail server's certificate is signed by your own certificate authority, mount that CA's PEM file into the web container and set `SSL_CERT_FILE` to its path there, for example in an extra Compose file passed with a second `-f`. Otherwise sends fail and the web log shows `could not send the email` with `class: tls`.

## Data retention

Raw telemetry expires after 90 days; infrastructure samples keep their separate 30-day retention. Cleanup runs in the background. Month-partitioned raw tables can retain rows for up to an additional month while waiting for the whole partition to expire. Postgres call summaries remain available after raw telemetry expires.

## Stop, upgrade and reset

```bash
make down                 # stop; your data is kept
VOICEGATEWAY_IMAGE=ghcr.io/mahimailabs/voicegateway:<new version> make up   # upgrade; migrations run on start
```

`docker compose -f deploy/compose.yaml --env-file deploy/.env down --volumes` deletes all stored data.

## What is not there yet

- **Some panels are not served yet**, such as project settings and provider probes: they say "Not served by this server yet".
- **Notifications** for alerts are not delivered yet.

Found something broken? [Open an issue](https://github.com/mahimailabs/voicegateway/issues/new/choose).
