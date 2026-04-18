# Deploy VoiceGateway to Fly.io

One-command deployment with public URL, persistent storage, and MCP-ready out of the box.

## Prerequisites

- [Fly.io account](https://fly.io/app/sign-up) (credit card required; new accounts get a limited trial before pay-as-you-go billing)
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
- Suspend-on-idle for cost savings when unused
- Auto-wake in ~2 seconds when traffic arrives

## Costs

Fly.io uses pay-as-you-go pricing. New accounts get a short trial (check [fly.io/docs/about/pricing](https://fly.io/docs/about/pricing/) for current details), after which usage is billed.

| Resource | Cost |
|---|---|
| shared-cpu-1x / 512MB VM | ~$1.94/month when running continuously |
| 1GB persistent volume | ~$0.15/month (billed even when machine is suspended) |
| Suspended machine | No compute charge, but volume storage still billed |

Use the [Fly pricing calculator](https://fly.io/calculator) for exact estimates. For light dev/test use, expect $1-3/month total.

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

- **Local models (Whisper, Kokoro, Piper, Ollama) not supported on default tier.** They need more RAM/CPU than shared-cpu-1x/512MB provides. Use Docker Compose on Hetzner or similar for local models.
- **Single-machine deploy by default.** For HA, set `min_machines_running = 2` and add regions.
- **CPU-only by default.** This template provisions shared CPU VMs. Fly does offer GPU Machines (deprecated, available until Aug 1 2026) which you can provision separately if needed for GPU-dependent models.

## Troubleshooting

### Deploy fails with "app name already taken"

`fly launch` generates a unique name. If you hit this, delete `fly.toml` and re-run `./deploy.sh`.

### MCP connection refused

`fly secrets list` confirms the secret **exists** but does not show its value. To verify/re-sync the token:

```bash
# Check the secret exists on Fly
fly secrets list | grep VOICEGW_MCP_TOKEN

# View your local token
cat .env.deploy

# Re-sync if they're out of date (e.g., after token rotation)
fly secrets set "VOICEGW_MCP_TOKEN=$(grep VOICEGW_MCP_TOKEN .env.deploy | cut -d= -f2)"
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
