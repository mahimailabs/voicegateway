---
title: "Auto-update the daemon"
description: "Keep a self-hosted VoiceGateway daemon on the latest patch release automatically, with rolling Postgres backups and effectively no interruption to your agents."
---
Once the daemon runs on your own box (see [Deploy to a VPS](/deployment/vps)), you can have it pull and apply new patch releases on its own. This page sets that up with an opt-in Compose overlay that adds a release-channel pin, [Watchtower](https://containrrr.dev/watchtower/), and rolling Postgres backups.

## Is it really "no interruption"?

For your agents, effectively yes. The SDK's `RemoteCollectorSink` buffers telemetry in memory and retries with backoff, and treats HTTP 429 as backpressure. During the few-second container swap of an update, agents buffer and re-send, so no agent call fails.

Two honest caveats:

- Cost telemetry is best-effort by design (it is not billing-of-record). A long daemon outage can drop some rows; a fast update swap does not.
- On a single node, a container recreate is not literally zero-downtime: the ingest endpoint and dashboard are unavailable for a few seconds. For true zero-downtime you need two daemon replicas behind a reverse proxy doing rolling restarts against shared Postgres (and migrations kept backward-compatible across the rollout). That is overkill for one box.

## The release channel: pin to a minor, never `:latest`

The release workflow publishes three Docker tags per version, for example `0.24.0`, `0.24`, and `latest`. The overlay pins the daemon to the minor channel `:0.24`:

- `:0.24` auto-gets bug-fix patches (`0.24.1`, `0.24.2`, ...) but never a breaking major. You move to `:0.25` deliberately.
- `:latest` would auto-pull the next major and run its migrations unattended. Do not auto-track `:latest` in production.

<Note>
A minor channel tag exists for every published minor. If you're running an older minor than `0.24`, pin the overlay to that minor's channel instead (for example `:0.23`) until you deliberately move up.
</Note>

## Turn it on

The overlay ([`docker-compose.autoupdate.yml`](https://github.com/mahimailabs/voicegateway/blob/main/docker-compose.autoupdate.yml)) layers on top of the daemon stack. From your deploy directory:

```bash
curl -fsSLO https://raw.githubusercontent.com/mahimailabs/voicegateway/main/docker-compose.autoupdate.yml
```

<Warning>
The overlay file in the repository still pins `mahimairaja/voicegateway:0.10`. Until that
is bumped, the file you just downloaded puts you on a channel twelve minors behind what
this page describes. Edit its `image:` line to `:0.24` before you bring the stack up, and
re-check it on your next pull.
</Warning>

```bash
docker compose -f docker-compose.collector.yml -f docker-compose.autoupdate.yml up -d
```

That starts three things alongside the daemon:

| Service | What it does |
| :-- | :-- |
| `collector` (relabeled) | Now tracks `mahimairaja/voicegateway:0.24` and carries the Watchtower-enable label. |
| `watchtower` | Checks hourly and recreates only the labeled daemon when `:0.24` advances, then removes the old image. Postgres and the backup sidecar are never auto-updated. |
| `db-backup` | A `postgres:16-alpine` sidecar that dumps the database on a schedule (matching server version, so `pg_dump` never refuses), keeping the most recent N dumps in a named volume. |

Always pass both `-f` files together for later `up`, `down`, and `logs`, so Compose keeps the merged definition.

## Tuning

Set these in your `.env` next to the Compose files:

| Variable | Default | Meaning |
| :-- | :-- | :-- |
| `VOICEGW_BACKUP_INTERVAL_SECONDS` | `21600` (6h) | How often the sidecar dumps. Lower it for a fresher pre-update snapshot. |
| `VOICEGW_BACKUP_KEEP` | `28` | How many dumps to retain (about 7 days at 6h). |

Watchtower's cadence is `WATCHTOWER_POLL_INTERVAL` (default `3600`, hourly) in the overlay.

## Restore from a backup

Dumps live in the `voicegw-backups` volume as `voicegw-<timestamp>.sql`. To roll back after a bad update:

<Steps>
  <Step title="Stop the daemon">
    Stop the daemon so nothing writes during the restore.

    ```bash
    docker compose -f docker-compose.collector.yml -f docker-compose.autoupdate.yml stop collector watchtower
    ```
  </Step>
  <Step title="List available dumps">
    ```bash
    docker compose -f docker-compose.collector.yml -f docker-compose.autoupdate.yml \
      exec db-backup sh -c 'ls -1t /backups/voicegw-*.sql'
    ```
  </Step>
  <Step title="Restore the chosen dump">
    ```bash
    docker compose -f docker-compose.collector.yml -f docker-compose.autoupdate.yml \
      exec -T db-backup sh -c 'psql -h postgres -U voicegw -d voicegw < /backups/voicegw-<timestamp>.sql'
    ```
  </Step>
  <Step title="Pin to the known-good version and restart">
    Edit the image tag in `docker-compose.autoupdate.yml` (for example `:0.24.1`), then bring the stack up:

    ```bash
    docker compose -f docker-compose.collector.yml -f docker-compose.autoupdate.yml up -d
    ```
  </Step>
</Steps>

<Warning>
Migrations run on daemon startup and are forward-only. The Compose healthcheck gates a bad boot, but the database is shared state: keep the backups (and ideally a periodic host-level snapshot of the box) so a migration regression is always recoverable.
</Warning>

## Opting back out

Bring the stack up with only the base file to drop Watchtower and the backup sidecar and return to a manually-pinned daemon:

```bash
docker compose -f docker-compose.collector.yml up -d
```
