# Self-hosting VoiceGateway

Run VoiceGateway on your own machine or server, connect your LiveKit project and see your agent's calls. This is the V1 preview: see [What is not there yet](#what-is-not-there-yet) before you start.

## What you need

- Docker with Compose v2 and at least 10 GB of free disk: the first build downloads Go and npm dependencies.
- `git` and `make`.
- A LiveKit project, on LiveKit Cloud or self-hosted: its URL, an API key and its secret.
- A voice agent built on LiveKit Agents for Python 1.8 or later.

## 1. Start the stack

```bash
git clone --branch v1.0.0-preview.8 --depth 1 https://github.com/mahimailabs/voicegateway.git
cd voicegateway
VOICEGATEWAY_IMAGE=ghcr.io/mahimailabs/voicegateway:1.0.0-preview.8@sha256:4e03820efd7e6d376cfbe782b87abed84d51487bb26ca8c7df94c414c16ac3d1 make up
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

The console's **Connect your agent** panel (on the project's Overview until the first call, and always in **Settings > Connection**) shows the two URLs to copy and whether each connection works. It builds them from `VOICEGATEWAY_PUBLIC_URL`, so add the address LiveKit and your agent reach VoiceGateway on to `deploy/.env` and run `make up` again:

```bash
VOICEGATEWAY_PUBLIC_URL=http://localhost:8080   # an agent on this machine; use your public HTTPS address otherwise
```

Until it is set, the panel says the public URL is not set instead of showing the URLs.

In the agent's environment, set:

```bash
LIVEKIT_OBSERVABILITY_URL=http://<voicegateway-host>:8080/ingest/<organisation-id>/<project-id>
```

and start the agent session with `record=True`; LiveKit Agents uploads nothing for a session that does not record. To keep the call audio out of the upload, use `record={"audio": False}` instead (Node: `record: { audio: false }`): traces, logs, metrics and the transcript still arrive. LiveKit Agents then uploads its traces, logs, metrics and session report to VoiceGateway, signed with the same LiveKit API key and secret. The agent's audio path is untouched: if VoiceGateway is down, calls carry on.

This setting sends the uploads to VoiceGateway instead of LiveKit Cloud's own observability, so those sessions no longer appear in LiveKit Cloud's dashboard.

## 5. Send LiveKit's webhooks to VoiceGateway

In LiveKit Cloud, open **Settings > Webhooks** and create a webhook with `https://<voicegateway-host>/ingest/<organisation-id>/<project-id>/livekit/webhook`, signed with the API key from step 3. Use the organisation and project IDs shown in the console URL. These IDs select the destination; LiveKit signatures still authenticate every request.

On a self-hosted LiveKit server, add the same URL to `livekit.yaml`, with the API key from step 3 as the signing key, then restart the LiveKit server:

```yaml
webhook:
  api_key: <the API key from step 3>
  urls:
    - https://<voicegateway-host>/ingest/<organisation-id>/<project-id>/livekit/webhook
```

Without the webhook, calls still appear, but they have no start or observed end time, duration or participants, because those come from LiveKit's room events. They stay **open** until two hours pass with no activity. VoiceGateway then closes them as inferred, with the last reported activity as the end time when there is one.

## Keep call recordings

Recordings are off for every new project, so the call audio in each agent upload is discarded. To keep it:

```bash
make project-recordings PROJECT=production SET=on
```

The agent's `record=True` sends the audio; VoiceGateway stores it in the S3 bucket (`VOICEGATEWAY_S3_*`, SeaweedFS in Compose) and plays it in the call view. Recordings are deleted 90 days after they are stored, like the rest of the raw call data. `SET=off` stops keeping new audio; what is stored stays until it expires.

A project can keep its recordings in your own S3 bucket instead, chosen only when you add the project and never changed afterwards: turn on "Keep recordings in my own S3 bucket" in the Add project form, or pass `RECORDING_BUCKET_ENDPOINT`, `RECORDING_BUCKET`, `RECORDING_BUCKET_ACCESS_KEY` and the secret in `RECORDING_BUCKET_SECRET_KEY` to `make project-add`. VoiceGateway writes and deletes one empty test object first, so the key needs to write, read, list and delete objects under `recordings/`.

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

## Sign in with Google or GitHub

Off unless you turn it on. Once on, people with an account, or with an invitation, can sign in with Google or GitHub instead of a password. It never lets in someone without an account or an invitation, and the provider must have verified their email.

1. Set `VOICEGATEWAY_PUBLIC_URL=https://<host>` (or `http://localhost:8080` on your own machine).
2. Run `docker compose exec web voicegateway preflight`. It prints each provider's callback URL, `https://<host>/auth/oauth/google/callback` and `https://<host>/auth/oauth/github/callback`.
3. Make an OAuth app with each provider you want, with that callback URL:
   - Google: Google Cloud console, APIs and services, Credentials, Create credentials, OAuth client ID, type Web application. Add the callback under Authorised redirect URIs.
   - GitHub: Settings, Developer settings, OAuth Apps, New OAuth App. Homepage URL is your public URL; Authorization callback URL is the callback.
4. Put the client id and secret in `deploy/.env` and run `make up` again:

```bash
VOICEGATEWAY_GOOGLE_CLIENT_ID=...
VOICEGATEWAY_GOOGLE_CLIENT_SECRET=...
VOICEGATEWAY_GITHUB_CLIENT_ID=...
VOICEGATEWAY_GITHUB_CLIENT_SECRET=...
```

Set both values of a pair or neither; web refuses to start with one. The sign-in and invitation pages then show the buttons.

### Or through Auth0

If you already run an Auth0 tenant, VoiceGateway can sign in through it instead, or as well. The tenant's own connections (Google, GitHub and others) decide how people prove who they are, and the same rules apply: an account or an invitation, and a verified email.

1. In Auth0, Applications, Create Application, type **Regular Web Application**.
2. Under Settings, set Allowed Callback URLs to `https://<host>/auth/oauth/auth0/callback`, the URL preflight prints.
3. Put the tenant's domain (Settings, Domain), the client id and the secret in `deploy/.env` and run `make up` again:

```bash
VOICEGATEWAY_AUTH0_DOMAIN=your-tenant.us.auth0.com
VOICEGATEWAY_AUTH0_CLIENT_ID=...
VOICEGATEWAY_AUTH0_CLIENT_SECRET=...
```

The domain is the bare host, without `https://`. Set all three or none.

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
