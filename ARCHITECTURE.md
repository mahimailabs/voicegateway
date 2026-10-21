# Architecture

<!--
How the system is built. Product intent lives in PRODUCT.md; visual design lives in DESIGN.md.
Each major choice links to a record in docs/decisions/.
-->

## Overview

VoiceGateway is one Go binary, `voicegateway`, run in two roles (0013): `web` takes data in and serves the dashboard and API, `worker` turns that data into calls and stores it. A Redis stream is the only link between them. Postgres, ClickHouse, Redis and an S3 bucket make up the rest, and `docker compose up` starts all six (0005, 0006, 0009).

## Components

| Component | Responsibility | Owns |
| --- | --- | --- |
| web | Checks keys, accepts events, webhooks, OTLP spans and LiveKit agents' observability uploads (0014), appends them to the stream and answers fast. Serves the API, MCP and the dashboard files. | Configuration and request state in Postgres only: list snapshots, and from 0027 accounts, sessions and sign-in counts. Reads Postgres and ClickHouse for queries; never writes ClickHouse |
| worker | Reads the stream, joins events into calls, writes batches to ClickHouse, scrapes server metrics, closes stale calls, applies retention. | All telemetry writes to ClickHouse, and all writes to the call index and call summaries |
| SDK (`sdk/python`) | `attach()` inside the agent, for frameworks without LiveKit's uploader and for data it lacks: sends turns, metrics and usage. Fails open. | Nothing |
| Dashboard (`dashboard/`) | React app, built in its own Docker stage and served from disk by web (0011). | Nothing |
| Postgres | Projects (each one LiveKit project, with its URL, key and encrypted secret, 0016), API keys, settings, the call index. | Configuration |
| ClickHouse | Events, turns, spans, usage and server metrics. | Everything measured |
| Redis | The ingest stream, the live view feed, and locks for one-at-a-time jobs (0007). | Data in flight |
| S3 bucket (SeaweedFS in Compose, 0026) | Recordings from the agent's session upload, or from LiveKit Egress, opt-in per project (0008, 0015), in the instance's bucket or one the project brought when it was created (0031). | Audio |

## Data Flow

1. LiveKit agents' observability uploads (0014), the SDK, LiveKit webhooks and OTLP spans reach web.
2. web checks the key or the webhook signature, appends the event to the Redis stream and answers.
3. worker reads the stream in a consumer group and matches each event to a call by room and time window, job id or trace id (0012).
4. worker writes in batches to ClickHouse and acknowledges the stream entries only after the write lands. Entries that fail repeatedly go to a dead-letter stream.
5. worker publishes call changes to Redis, and web pushes them to the live view.
6. On a timer, worker scrapes SFU, SIP and node metrics, and closes calls whose end never arrived, marked as inferred.
7. The dashboard and MCP query web, which reads Postgres and ClickHouse. web writes configuration and request state, to Postgres only: a list's snapshot, and from 0027 accounts, sessions and sign-in counts.

## Boundaries

- web never writes to ClickHouse; worker never answers HTTP traffic.
- The Redis stream format is the only contract between web and worker.
- The SDK never blocks or fails the call; capture errors are dropped and counted.
- Nothing logs raw webhook bodies; they carry a TURN password.
- A missing value is stored as unknown, never as zero.
- Code outside `internal/` packages cannot import them; the public surface is the HTTP API, MCP and the SDK.

## Stack

| Layer | Choice | Decision |
| --- | --- | --- |
| Core | Go, one binary, constructor injection | docs/decisions/0009-go-core.md |
| Roles | web and worker from one image | docs/decisions/0006-web-and-worker-roles.md |
| Queue | Redis streams | docs/decisions/0007-redis-streams-for-ingest.md |
| Storage | Postgres, ClickHouse, any S3 bucket (SeaweedFS in Compose, 0026) | docs/decisions/0005-self-hosted-platform-only.md |
| Libraries | chi, go-redis v9, clickhouse-go v2, pgx v5 with sqlc, goose, livekit/protocol, OTel pdata, slog, errgroup, cobra | docs/decisions/0009-go-core.md |
| Dashboard | React, served from files in the image | docs/decisions/0011-dashboard-served-from-files.md |
| SDK | Python, `attach()` for any agent framework | docs/decisions/0002-framework-agnostic-attach.md |

Exact versions are pinned in `go.mod`, `deploy/compose.yaml` and the SDK's lock file.

## Repository Layout

Flat, one component per package, named for what it does. Only contracts are shared (`event`, `stream`, `store`); there is no `utils/`, `common/` or `shared/`.

```
voicegateway/
├── cmd/voicegateway/      main.go: web | worker | migrate | preflight
├── internal/
│   ├── app/               wiring only: web.go, worker.go
│   ├── config/            Config
│   ├── preflight/         Preflight
│   ├── event/             Envelope, Kind (shared contract)
│   ├── stream/            Producer, Consumer (the web to worker contract)
│   ├── ingest/            Webhooks, uploads: verify against the project's LiveKit key, append
│   ├── auth/              Gate
│   ├── decode/            Decoder: records.go, otlp.go, livekit.go
│   ├── route/             Router, dead letters
│   ├── usage/             Meter
│   ├── call/              Resolver
│   ├── trace/             Linker
│   ├── sink/              Sink
│   ├── live/              Broadcaster
│   ├── scrape/            Scraper
│   ├── bucket/            Bucket: recordings in S3, ranged reads, 90-day expiry by day
│   ├── settle/            Settler, Closer, Rollup, Retention, RecCheck
│   ├── api/               dashboard API
│   ├── mcp/               MCP tools
│   ├── ui/                serves the dashboard files
│   ├── store/             storage interfaces (shared contract)
│   │   ├── pgstore/       Postgres, sqlc
│   │   ├── chstore/       ClickHouse
│   │   └── storetest/     in-memory fake for unit tests
│   └── obs/               logs, own metrics, /healthz
├── ee/                    commercial code, same package names as internal/
├── dashboard/src/features/   one folder per page: calls/ call/ sfu/ sip/ live/ settings/
├── sdk/python/            attach(), with its own packaging and tests
├── migrations/            postgres/ and clickhouse/
├── test/                  end-to-end: full stack plus livekit-server --dev, fixtures
├── deploy/                compose.yaml, Dockerfile
├── docs/                  decisions/, specs/, progress.md
└── Makefile               make dev | test | lint | build for Go, the dashboard and the SDK
```

Every package has the same shape: `doc.go` (its job, the rule it keeps, its decision), a file named after its main type, `store.go` for the interfaces it needs, its tests and `testdata/`. Anything that does I/O keeps its interface, real implementation and test fake side by side. Folders never exist only to group.

## Data Model

- **Project**: owns API keys and settings. Every row below belongs to one project.
- **Call**: one conversation in one room, from start to end. Has a room, time window, optional job id, and an end that is either observed or inferred.
- **Participant**: a person, agent or SIP leg in a call.
- **Turn**: one exchange, with STT, LLM and TTS latency and usage (tokens, audio seconds, characters).
- **Span**: an OTLP span, linked to a call and turn through its trace id.
- **Server sample**: CPU, memory and network of an SFU, SIP or agent node at a point in time.
- **Recording**: a link to an audio file in the bucket.

## External Services

- **LiveKit server, SIP and Egress**: the source of webhooks, metrics and recordings. If they are down, no new data arrives; VoiceGateway keeps serving what it has.
- **Model providers**: never called by VoiceGateway. Usage comes from the agent.
- No other outside service is needed. Nothing leaves the user's servers.

## Non-Functional Targets

Proposed, for review:

- web answers an ingest request in under 50 ms at p99.
- No event is lost at 1,000 concurrent calls.
- A call appears in the live view within 2 seconds of the event.
- The SDK adds under 1 ms per turn to the agent.

## Security and Privacy

- API keys per project, stored hashed in Postgres. LiveKit webhooks are checked against their signature.
- Secrets come from environment variables, never from files in the repo.
- Transcripts and recordings are personal data. They stay in the user's own storage, with retention set per project.

## Deployment

- One `deploy/compose.yaml` starts all six services. The image is published per release.
- Upgrades run `voicegateway migrate` before the new roles start. Rolling back means the previous image tag; migrations only add.
- VoiceGateway Cloud runs the same image, one stack per region, with its control plane (sign-up, provisioning, Stripe billing) in the private `voicegateway-cloud` repository (0017, 0024).

## Observability

- Structured logs (slog) from both roles, with call and request ids.
- Prometheus metrics: stream lag, batch sizes, write errors, dead-letter count, unmatched events.
- `voicegateway preflight` checks every dependency before start.
