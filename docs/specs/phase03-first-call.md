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

Slice B is built in three PRs: (a) webhooks to the stream, (b) the Agents observability uploads, (c) the worker into ClickHouse. All three are specified below.

Slice B (a), webhooks to the stream. LiveKit posts to `POST /livekit/webhook`; the `Authorization` JWT is checked with livekit/protocol `webhook.Receive` (v1.52.1), which finds the secret by the token's issuer (the LiveKit API key) and compares the body's SHA-256 with the token's claim. The stream entry layout is version 1 of `internal/stream`.

6. Given a webhook signed by a project's LiveKit key and secret, when it is posted, then it answers 200 and one envelope is on the stream, attributed to that project, with LiveKit's event id and the body unchanged (measured by `TestWebhookVerified`).
7. Given a webhook with no signature, a malformed token, an unknown key, the wrong secret or a body changed after signing, when it is posted, then it answers 401 and nothing is appended; a body over 1 MiB answers 413 (measured by `TestWebhookVerified`).
8. Given Redis or the project lookup down, when a signed webhook is posted, then it answers 503 so LiveKit retries, never 200 or 401, and nothing is appended (measured by `TestWebhookStreamDown`, `TestWebhookProjectLookupDown`).
9. Given envelopes appended before the worker's group exists, when the group is created and read, then they are delivered unchanged, stay pending until acknowledged, and an entry in an unknown layout is returned with its id and an error instead of being dropped (measured by `TestAppendReadAck`, `TestReadUnknownLayout`).

Slice B (b), the Agents observability uploads, at LiveKit's paths as LiveKit Agents 1.8.7 sends them (`livekit/agents/telemetry/traces.py`): OTLP over HTTP, gzip, to `/observability/{traces,logs,metrics}/otlp/v0`, and a multipart session report (`header` MetricsRecordingHeader, `chat_history` JSON, `audio` recording.ogg) to `/observability/recordings/v0`. Each carries `Authorization: Bearer` with a JWT from the agent's LiveKit key and secret holding the observability write grant, and no body hash. Until a project has a recordings bucket (0015), the audio part is read and discarded and the report says so.

10. Given an OTLP export with a bearer JWT from a project's key, signed with its secret and holding the write grant, when it is posted, then it answers 200 with an empty Export response and one envelope of that signal's kind is on the stream, decompressed, attributed, with no event id (measured by `TestUploadOTLPVerified`).
11. Given no token, a token without the `Bearer ` scheme, an unknown key or the wrong secret, then 401; a valid token without the write grant, then 403; a body that is not gzip, then 400; one that decompresses past 16 MiB, then 413; in every case nothing is appended (measured by `TestUploadOTLPVerified`, `TestUploadOTLPBadBody`).
12. Given a session upload with header, chat history and audio, when it is posted, then one envelope identified by the job id holds the header and the chat history byte for byte, marks the audio as discarded, and no audio byte reaches the stream (measured by `TestUploadSessionReport`).
13. Given the stream or the project store down, when an upload is posted, then it answers 503 with a google.rpc.Status carrying RetryInfo, the only 5xx the Agents session uploader retries, and no refusal carries the phrases that make the agent stop uploading for good (measured by `TestUploadUnavailableRetries`, `TestUploadRefusalDoesNotDisableAgent`).

Slice B (c), the worker into ClickHouse. `voicegateway migrate` also applies `migrations/clickhouse` (goose, ClickHouse dialect). `worker` reads `voicegateway:ingest` in group `worker` as a consumer named by its host, claims entries another consumer left unacknowledged for a minute, and stores each batch in `events`. The dedupe key is the source's event id, else `entry:` plus the stream entry id; keys already stored are skipped, and ReplacingMergeTree folds any duplicate two racing workers write. Acknowledging deletes the entry (XACKDEL ACKED). LiveKit's `room.turnPassword` is removed from webhook bodies before they are stored (maintainer decision, 2026-10-16).

14. Given webhooks and agent uploads on the stream, when the worker runs, then each is one row attributed to its project, an unknown event id stays empty, and the stream and the group's pending list are empty (measured by `TestSinkStoresAndAcks`).
15. Given the same webhook delivered twice in one batch and again after a restart, then one row holds it (measured by `TestSinkSkipsStoredWebhook`).
16. Given a webhook whose room carries a TURN password, when it is stored, then the stored body has no `turnPassword` and keeps the rest (measured by `TestSinkStripsTURNPassword`).
17. Given entries a worker read and never acknowledged, when another worker runs, then it claims and stores them once (measured by `TestSinkClaimsFromDeadWorker`, `TestClaimFromDeadConsumer`).
18. Given an entry that cannot be decoded, then it moves to `voicegateway:ingest:dead` with its fields and the reason, and the entries after it are stored (measured by `TestSinkDeadLettersUndecodable`, `TestReadUnknownLayout`).
19. Given ClickHouse unreachable, then nothing is acknowledged or dead-lettered, and once it answers every entry is stored (measured by `TestSinkAcksOnlyAfterInsert`).
20. Given acknowledged entries, then they are deleted from the stream, so it does not grow (measured by `TestAppendReadAck`).
21. Given a batch in which ClickHouse refuses one row for its data, then only that row is dead-lettered and the rest are stored; a failure that does not blame the data is retried and dead-letters nothing (measured by `TestSinkDeadLettersOnlyRefusedRows`, `TestSinkAcksOnlyAfterInsert`).
22. Given a webhook dead-lettered with a TURN password in a body that does not parse, then the dead-lettered body has the password blanked (measured by `TestDeadLetterBlanksTURNPassword`).

## Manual Check

`make up`, then `voicegateway project add` with a real LiveKit Cloud project, then `psql` shows the project with a sealed secret and no plaintext.

Slice B (a): add a project for `livekit-server --dev` (key `devkey`), run the server with `webhook.urls` set to `http://localhost:8080/ingest/default/<project-id>/livekit/webhook`, create and delete a room with `lk`, then `XRANGE voicegateway:ingest - +` shows the room_started and room_finished envelopes for that project.

Slice B (b): add a project for a LiveKit Cloud test project, run a LiveKit Agents 1.8.7 agent with `LIVEKIT_OBSERVABILITY_URL=http://localhost:8080/ingest/default/<project-id>` and `record=True` in a test room until it shuts down, then the stream holds `livekit.agent.traces`, `.logs`, `.metrics` and one `livekit.agent.session_report` for that project.

Slice B (c): with the worker stopped, create and delete a room on `livekit-server --dev`; the stream holds two entries. Start the worker: the stream empties and `events` gains two rows for the project. `make down` then `make up`: the row count is unchanged and `count()` equals `count() FINAL`.
