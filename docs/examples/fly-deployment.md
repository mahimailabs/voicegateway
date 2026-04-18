# Deploy to Fly.io

Deploy VoiceGateway to Fly.io with one command. Get a public HTTPS URL with persistent storage and MCP endpoint in under 5 minutes.

## Overview

| Feature | Details |
|---------|---------|
| Time to deploy | ~3 minutes first time, ~30s redeploys |
| Cost | ~$1-3/month pay-as-you-go ([pricing calculator](https://fly.io/calculator)) |
| HTTPS | Automatic via Fly's edge |
| Storage | 1GB persistent volume (~$0.15/month, billed even when suspended) |
| MCP | SSE endpoint at `/mcp/sse` with bearer auth |
| Cold start | ~2 seconds from suspended state |

## Prerequisites

1. A [Fly.io account](https://fly.io/app/sign-up) (free tier, credit card required)
2. Install flyctl:

::: code-group
```bash [macOS]
brew install flyctl
```
```bash [Linux]
curl -L https://fly.io/install.sh | sh
```
:::

3. Log in:
```bash
fly auth login
```

## Step-by-step deployment

### 1. Clone and deploy

```bash
git clone https://github.com/mahimailabs/voicegateway
cd voicegateway/deploy/fly
./deploy.sh
```

### 2. Expected output

```
→ VoiceGateway → Fly.io deployment

✓ Authenticated as you@example.com
✓ Generated new MCP token (saved to .env.deploy)
→ First-time deployment. Creating Fly app...
✓ Created app: voicegw-happy-canyon-4723 in region ord
→ Creating persistent volume for SQLite storage...
→ Setting MCP bearer token as Fly secret...
→ Deploying (this takes 2-3 minutes)...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓ VoiceGateway is live!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Dashboard:    https://voicegw-happy-canyon-4723.fly.dev
  MCP endpoint: https://voicegw-happy-canyon-4723.fly.dev/mcp/sse
  MCP token:    voicegw_mcp_a7f3e2b8c9d1...
```

### 3. Verify

```bash
# Health check
curl https://voicegw-happy-canyon-4723.fly.dev/health

# Dashboard — open in browser
open https://voicegw-happy-canyon-4723.fly.dev
```

## Configure via Claude Code

### Add the MCP server

```bash
claude mcp add voicegateway \
  --transport sse \
  --url https://voicegw-happy-canyon-4723.fly.dev/mcp/sse \
  --header "Authorization: Bearer voicegw_mcp_a7f3e2b8c9d1..."
```

### First commands

Once connected, ask Claude Code:

1. "List my voicegateway providers" — shows empty state
2. "Add Deepgram with API key dg_live_abc123" — registers and tests the provider
3. "Register deepgram/nova-3 as an STT model" — makes it available for routing
4. "Create a project called test-app with a $5 daily budget" — sets up cost tracking
5. "Show me today's costs for test-app" — confirms everything is wired

## Costs and scaling

### Expected monthly costs

Fly.io uses pay-as-you-go pricing. New accounts get a limited trial (2 VM-hours or 7 days), after which all usage is billed. See [Fly's pricing docs](https://fly.io/docs/about/pricing/) for current details.

| Resource | Cost |
|----------|------|
| shared-cpu-1x / 512MB VM | ~$1.94/month continuously |
| 1GB persistent volume | ~$0.15/month (billed even when suspended) |
| Light production (<1K req/day) | ~$2-3/month total |
| Moderate production (1K-10K req/day) | ~$3-8/month |

### Scaling up

```bash
# More memory
fly scale vm shared-cpu-2x --memory 1024

# Always-on (no cold-start)
# Edit deploy/fly/fly.toml: min_machines_running = 1

# Multiple regions
fly regions add fra syd
```

## Limitations

- **No local models on default tier.** Whisper, Kokoro, Piper, and Ollama require more RAM than 512MB. For local models, use [Docker Compose](/examples/docker-deployment) on a larger machine.
- **Single machine by default.** For high availability, increase `min_machines_running` and add regions.
- **CPU-only by default.** This template provisions shared CPU VMs. Fly does offer GPU Machines (deprecated, available until Aug 1 2026) which you can provision separately if needed.

## Troubleshooting

### Deploy hangs or fails

Check Fly status and logs:
```bash
fly status
fly logs
```

### MCP tools return errors

1. View your local token: `cat deploy/fly/.env.deploy`
2. Confirm the secret exists on Fly: `fly secrets list | grep VOICEGW_MCP_TOKEN` (this shows the secret exists but not its value)
3. Re-sync if needed: `fly secrets set "VOICEGW_MCP_TOKEN=$(grep VOICEGW_MCP_TOKEN deploy/fly/.env.deploy | cut -d= -f2)"`
4. Test manually: `curl -H "Authorization: Bearer <token>" https://<app>.fly.dev/mcp/sse`

### Data lost after redeploy

Data is stored on the persistent volume at `/data`. Check that the mount exists:
```bash
fly volumes list
```

## Related

- [Docker Compose Deployment](/examples/docker-deployment) — self-hosted on any Docker host
- [MCP Setup](/mcp/setup) — configure Claude Code, Cursor, and other agents
- [Configuration Reference](/configuration/voicegw-yaml) — customize your gateway
