# Deploy VoiceGateway to Fly.io

One-command deployment with public URL, persistent storage, and MCP-ready out of the box.

## Prerequisites

- [Fly.io account](https://fly.io/app/sign-up) (free tier available, credit card required)
- [flyctl](https://fly.io/docs/hands-on/install-flyctl/) installed
- Authenticated: `fly auth login`

## Deploy

```bash
./deploy.sh
```

First-time deployment takes ~3 minutes. Redeploys take ~30 seconds.

## What you get

- Public HTTPS URL: `https://<generated-name>.fly.dev`
- Dashboard at the root URL
- MCP SSE endpoint at `/mcp/sse` with bearer auth
- Persistent 1GB volume for SQLite + encryption secret
- Suspend-on-idle (costs near $0 when unused)
- Auto-wake in ~2 seconds when traffic arrives

## Costs

| Usage pattern | Monthly cost |
|---|---|
| Idle / light dev use | $0-$2 (within Fly's included allowances) |
| Moderate traffic (1K-10K requests/day) | $2-$8 |
| Heavy traffic | Scale up (see below) |

Fly's free allowances include 3 shared-cpu-1x VMs and 3GB storage. VoiceGateway uses 1 VM + 1GB volume, fitting comfortably within the free tier for light use. Costs accrue beyond the allowance.

## Scaling up

```bash
# More RAM for high concurrency
fly scale vm shared-cpu-2x --memory 1024

# Multi-region for lower latency
fly regions add fra syd

# Keep warm (no cold-start delay)
# Edit fly.toml: min_machines_running = 1
```

## Limitations

- **Local models (Whisper, Kokoro, Piper, Ollama) not supported on default tier.** They need more RAM/CPU and won't fit on shared-cpu-1x/512MB. Use Docker Compose on Hetzner or similar for local models.
- **Single-machine deploy by default.** For HA, set `min_machines_running = 2` and add regions.
- **No GPU.** Fly doesn't offer GPU instances. GPU-dependent models need a separate host.

## Troubleshooting

### Deploy fails with "app name already taken"

`fly launch` generates a unique name. If you hit this, delete `fly.toml` and re-run `./deploy.sh`.

### MCP connection refused

Verify the token matches:
```bash
fly secrets list
cat .env.deploy
```

### Machine won't start

Check logs:
```bash
fly logs
```

### Dashboard shows empty

The managed tables are empty until you add a provider. Use Claude Code:
```
> Add Deepgram provider with key <yours>
```

Or SSH in:
```bash
fly ssh console
voicegw status
```

## Alternate deployments

- [Docker Compose](../../README.md) - local or self-hosted on any Docker host
