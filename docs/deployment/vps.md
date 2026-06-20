---
title: "Deploy to a VPS"
description: "Run the VoiceGateway fleet collector on your own server with Docker Compose and Caddy for automatic HTTPS."
---

# Deploy to a VPS

Choose this path when you already control a server (cheapest option; ideal for co-locating with a self-hosted LiveKit server).

## Prerequisites

- A VPS with Docker and Compose installed (`curl -fsSL https://get.docker.com | sh`)
- A domain you can point at the box

## Deploy the collector

```bash
mkdir -p ~/voicegw && cd ~/voicegw
curl -fsSLO https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docker-compose.collector.yml
sed -i 's#mahimairaja/voicegateway:latest#mahimairaja/voicegateway:0.9.2#' docker-compose.collector.yml

printf 'VOICEGW_PG_PASSWORD=%s\n' "$(openssl rand -hex 24)" > .env

# Ingest key the agents will present (must NOT start with vk_).
INGEST_KEY="$(openssl rand -hex 32)"
cat > voicegw.yaml <<EOF
auth:
  api_keys:
    - token: "${INGEST_KEY}"
      name: fleet-agents
      scopes: [write]
EOF
echo "AGENT KEY (use as the agent virtual_key): ${INGEST_KEY}"

docker compose -f docker-compose.collector.yml up -d
sleep 10 && curl -fsS http://localhost:8080/health && echo     # -> ok
```

Postgres runs as a service in this Compose (self-hosted). To use a managed database instead, drop the `postgres` service and set `VOICEGW_DB_URL=postgresql+asyncpg://...` (e.g. a Neon URL) on the `collector` service.

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

```caddyfile
collector.<your-domain> {
    reverse_proxy localhost:8080
}
```

Reload Caddy and open the firewall:

```bash
sudo systemctl reload caddy
sudo ufw allow 80,443/tcp
```

For safety, bind the collector to localhost only by changing the compose port mapping to `127.0.0.1:8080:8080` so only Caddy is internet-facing. Point a DNS A record `collector.<your-domain>` at the VPS IP.

### Reuse an existing reverse proxy

If the box already runs a reverse proxy on 80/443 (for example a self-hosted LiveKit server whose Caddy runs with host networking), that proxy reaches the collector at `localhost:8080` with no extra wiring. The collector publishes 8080 on the host; a host-networked proxy shares the host's network namespace. Add a vhost or TLS-SNI route for `collector.<your-domain>` pointing to `localhost:8080`. Back up the proxy config first and reload it gracefully.

## Security

::: warning
Only `/v1/ingest` and `/health` need to be public. Put the dashboard and `/v1/virtual-keys` behind basic auth at the proxy level, or reach them via an SSH tunnel from your local machine.
:::

## Verify

Follow the steps at [Verify](/docs/deployment#verify), using `https://collector.<your-domain>` as the collector URL and the `AGENT KEY` printed above.

## Connect your agent

See [Connect your agent](/docs/deployment#connect-your-agent). Use `https://collector.<your-domain>` as `collector_url` and the `AGENT KEY` as `virtual_key`.
