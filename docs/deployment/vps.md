---
title: "Deploy to a VPS"
description: "Run the VoiceGateway daemon on your own server with Docker Compose and Caddy for automatic HTTPS."
---
Choose this path when you already control a server (cheapest option; ideal for co-locating with a self-hosted LiveKit server).

## One-line installer (recommended)

The installer script handles Docker, secrets, image pinning, health checking, and HTTPS in a single command:

```bash
curl -fsSL https://voicegateway.dev/collector.sh | bash
```

It prompts for backend (SQLite or Postgres) and whether to set up HTTPS. For non-interactive use:

```bash
# SQLite (single collector, no external database)
curl -fsSL https://voicegateway.dev/collector.sh | bash -s -- --sqlite --yes

# Postgres (fleet / production), expose via https://collector.example.com
curl -fsSL https://voicegateway.dev/collector.sh | bash -s -- --postgres --domain example.com --yes
```

The script:
- Installs Docker if not present (with confirmation)
- Generates and persists the ingest key and Postgres password on first run, reuses them on later runs (no password-regen footgun)
- Pins the image to the latest release version (never `:latest`)
- Health-checks the container before returning
- Installs Caddy and issues a certificate automatically if ports 80/443 are free and a domain is given; otherwise prints a reverse-proxy snippet for your existing proxy

The ingest key is printed to your terminal and saved to the deploy directory (`/opt/voicegateway/voicegw.yaml` by default). Use it as the `api_key` when connecting agents.

## Manual setup

Prerequisites: Docker and Compose installed (`curl -fsSL https://get.docker.com | sh`).

<Steps>

### Download Compose file and generate secrets

```bash
mkdir -p ~/voicegw && cd ~/voicegw
curl -fsSLO https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docker-compose.collector.yml
sed -i 's#mahimairaja/voicegateway:latest#mahimairaja/voicegateway:0.22.3#' docker-compose.collector.yml

printf 'VOICEGW_PG_PASSWORD=%s\n' "$(openssl rand -hex 24)" > .env
```

### Create the ingest key

The ingest key must not start with `vk_`.

```bash
INGEST_KEY="$(openssl rand -hex 32)"
cat > voicegw.yaml <<EOF
auth:
  api_keys:
    - token: "${INGEST_KEY}"
      name: fleet-agents
      scopes: [write]
EOF
echo "AGENT KEY (use as the agent api_key): ${INGEST_KEY}"
```

### Start the stack

```bash
docker compose -f docker-compose.collector.yml up -d
sleep 10 && curl -fsS http://localhost:8080/health && echo     # -> ok
```

<Warning>
Run the above exactly once. Re-running it regenerates the Postgres password but the existing volume keeps the old one, causing authentication failures. If you need to re-run, bring the stack down first and remove the volume: `docker compose down -v`. The one-line installer avoids this footgun by persisting secrets across runs.
</Warning>

Postgres runs as a service in this Compose (self-hosted). To use a managed database instead, drop the `postgres` service and set `VOICEGW_DB_URL=postgresql+asyncpg://...` (for example a Neon URL) on the `collector` service.

</Steps>

## Expose over HTTPS

### Fresh box (install Caddy)

Install Caddy via the official apt repository:

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy
```

Create `/etc/caddy/Caddyfile`:

```text
collector.<your-domain> {
    reverse_proxy localhost:8080
}
```

Reload Caddy and open the firewall:

```bash
sudo systemctl reload caddy
sudo ufw allow 80,443/tcp
```

For safety, bind the daemon to localhost only by changing the compose port mapping to `127.0.0.1:8080:8080` so only Caddy is internet-facing. Point a DNS A record `collector.<your-domain>` at the VPS IP.

### Reuse an existing reverse proxy

If the box already runs a reverse proxy on 80/443 (for example a self-hosted LiveKit server whose Caddy runs with host networking), it reaches the daemon at `localhost:8080` with no extra wiring: the daemon publishes 8080 on the host, and a host-networked proxy shares the host's network namespace. Add a vhost or TLS-SNI route for `collector.<your-domain>` pointing to `localhost:8080`. Back up the proxy config first and reload it gracefully.

For LiveKit's layer-4 Caddy (structured `caddy.yaml`), add a TLS-SNI route and include the hostname in `apps.tls.certificates.automate`:

```text
# Under apps.layer4.servers.main.routes:
          - match:
              - tls: { sni: ["collector.<your-domain>"] }
            handle:
              - handler: tls
                connection_policies: [{ alpn: ["http/1.1"] }]
              - handler: proxy
                upstreams: [{ dial: ["localhost:8080"] }]
```

Reload with `caddy reload --config /etc/caddy.yaml --adapter yaml` (validates before applying; LiveKit stays up if the config is invalid).

## Security

<Warning>
Only `/v1/ingest`, `/health`, and (if you point LiveKit webhooks at VoiceGateway) `/v1/livekit/webhook` need to be public. Put the dashboard and `/v1/api-keys` behind basic auth at the proxy level, or reach them via an SSH tunnel from your local machine.

`/v1/livekit/webhook` carries no bearer token because LiveKit cannot send one: it authenticates every request by verifying the LiveKit webhook signature against your API key and secret, and rejects the request before reading the body. It fails closed, so a deployment with no LiveKit credentials configured rejects every webhook rather than accepting unsigned writes.
</Warning>

## Verify

Follow the steps at [Verify](/deployment/index#verify), using `https://collector.<your-domain>` as the daemon URL and the ingest key printed during setup.

## Connect your agent

See [Connect your agent](/deployment/index#connect-your-agent). Use `https://collector.<your-domain>` as `collector_url` and the ingest key as `api_key`.
