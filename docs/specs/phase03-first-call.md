# Phase 03: First real call

## Goal

A teammate adds their LiveKit project to a running stack, points a LiveKit agent and the server's webhooks at it, makes one call, and that call is stored once, with its events joined to it, and survives a restart. This phase is built in two slices so each PR stays reviewable.

## Context

- PRODUCT.md capabilities 1 and 4; principles 2 (unknown stays unknown) and 3 (never lose data quietly).
- ARCHITECTURE.md: Data Flow steps 1 to 4, Data Model, Boundaries.
- Decisions 0007 (Redis stream, ack after the stream), 0012 (call identity, dedupe on event id), 0014 (observability endpoints, JWT against the project's LiveKit key), 0016 (a project is a LiveKit project; the secret is encrypted at rest).
- `docs/specs/v1-connect-plan.md`: "Capture the first real call".

## In Scope

Slice A, storage and projects:

- `migrations/postgres` applied by `voicegateway migrate` with goose, from migrations embedded in the binary; Compose runs it once before web and worker start.
- Organisations and projects in Postgres. A project holds its LiveKit URL, API key and API secret; the secret is sealed with AES-256-GCM under `VOICEGATEWAY_SECRET_KEY` and never returned by any read.
- `voicegateway project add`: creates a project from the LiveKit URL, key and secret (secret from `LIVEKIT_API_SECRET`, never a flag), and refuses a LiveKit API key that another project already uses, since ingest finds the project by that key.
- `internal/store` (the Project record) and `internal/store/pgstore` (sqlc queries).

Slice B, ingest to storage:

- `web` accepts LiveKit webhooks and the Agents observability uploads, verifies each against the project's LiveKit key and secret, and appends an envelope to the Redis stream; a bad signature is 401 and nothing is appended.
- `worker` reads the stream in a consumer group, writes events to ClickHouse in batches, acknowledges only after the insert, and skips a webhook already stored under the same event id.
- `migrations/clickhouse` for the event table.

## Out of Scope

- Joining events into calls and turns, the read API and connecting the console (the next phase).
- VoiceGateway API keys for the SDK and MCP, login, members and roles.
- Recordings (0015), Egress, SIP and scraping.

## Constraints

- Libraries from decision 0009: pgx v5 with sqlc, goose, livekit/protocol for webhook and token verification, go-redis v9, clickhouse-go v2. sqlc and goose are `tool` directives.
- Never log a raw webhook or upload body, a LiveKit secret or a sealed value.
- Migrations only add (ARCHITECTURE.md Deployment).

## Acceptance Checks

Slice A:

1. Given empty Postgres, when `voicegateway migrate` runs twice, then the first run applies every migration and the second applies none, both exiting 0 (measured by `TestMigrateTwice`).
2. Given a project added with a LiveKit secret, when its row is read from Postgres, then the stored value is not the secret, and opening it with the same key returns the secret (measured by `TestProjectSecretSealed`).
3. Given a second project with a LiveKit API key already in use, when it is added, then it is refused with an error naming the key's project, and nothing is written (measured by `TestProjectDuplicateLiveKitKey`).
4. Given a sealed secret and a different `VOICEGATEWAY_SECRET_KEY`, when it is opened, then a named error is returned, never a panic or the wrong plaintext (measured by `TestOpenWithWrongKey`).
5. Given `make up`, when the stack starts, then `migrate` finishes before web and worker start (measured by the CI compose job).

Slice B: checks are written with that slice.

## Manual Check

`make up`, then `voicegateway project add` with a real LiveKit Cloud project, then `psql` shows the project with a sealed secret and no plaintext.
