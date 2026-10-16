# Phase 02: Runnable foundation

## Goal

A teammate clones the repository, runs `make up`, and gets the six-part stack running from pinned images: Postgres, ClickHouse, Redis, an S3 bucket (SeaweedFS, decision 0026) and the `voicegateway` binary as `web` and `worker`. `voicegateway preflight` says which dependency is unreachable instead of failing later.

## Context

- ARCHITECTURE.md: Overview, Components, Repository Layout, Stack, Deployment, Observability.
- Decisions 0005 (platform only), 0006 (web and worker roles), 0007 (Redis streams), 0009 (Go core and its libraries), 0013 (binary named `voicegateway`).
- `docs/specs/v1-connect-plan.md`: the "Make preview installs repeatable" outcome. Ingest, call joining and migrations with tables are the next phases.

## In Scope

- `go.mod` at the root with exact `go` and `toolchain` lines and dev tools as `tool` directives (golangci-lint, gofumpt).
- `cmd/voicegateway`: one binary, subcommands `web`, `worker` and `preflight` (cobra).
- `internal/config`: configuration from `VOICEGATEWAY_*` environment variables, validated at start, never logged with secrets.
- `internal/preflight`: checks Postgres, ClickHouse, Redis and the S3 bucket, each with a timeout, and reports every failure, not only the first.
- `internal/obs`: slog JSON logs and `/healthz`.
- `internal/app`: wiring for `web` (HTTP server with `/healthz`, graceful shutdown) and `worker` (runs until stopped; no consumers yet).
- `deploy/compose.yaml`: the stack with every image pinned by tag and digest; `deploy/Dockerfile` for the binary.
- Root Makefile targets: `up`, `down`, `go-test`, `go-lint`; `test` and `lint` also run the Go checks.
- CI job that builds, lints and tests the Go code, including tests against real dependencies through testcontainers-go.

## Out of Scope

- Database tables and `voicegateway migrate` (next phase, with project and key storage).
- Ingest, webhooks, spans, the Redis stream contract and call joining.
- The dashboard, its API and serving its files.
- The legacy Python product and its root `docker-compose*.yml`, which stay until the replacement release.

## Constraints

- Libraries from decision 0009 only: cobra, pgx v5, clickhouse-go v2, go-redis v9, the MinIO Go client (any S3 bucket), slog, errgroup, testcontainers-go. No dependency injection container.
- Flat packages under `internal/`; interfaces live in the package that uses them; wiring only in `internal/app`.
- Never log a password, key or full connection URL.

## Acceptance Checks

1. Given a fresh clone with Docker, when `make up` runs, then the four dependencies pass their health checks, web and worker are running, and `curl localhost:8080/healthz` answers 200 (measured by the CI compose smoke job, 1 of 1 run).
2. Given all four dependencies reachable, when `voicegateway preflight` runs, then it exits 0 and prints one OK line per dependency (measured by `TestPreflightAllReachable` against testcontainers).
3. Given Redis stopped, when `voicegateway preflight` runs, then it exits non-zero and names Redis, and still reports the other three (measured by `TestPreflightReportsEveryFailure`).
4. Given a required variable missing, when any subcommand starts, then it exits non-zero naming the variable, and no secret appears in the output (measured by `TestConfigMissingAndRedacted`).
5. Given `deploy/compose.yaml`, when it is searched, then every `image:` carries a `@sha256:` digest (measured by `TestComposeImagesPinned`, 0 unpinned).
6. Given the Go code, when `make go-lint` and `go test -race ./...` run, then both pass with 0 findings (measured by CI).

## Manual Check

On a clean machine, `make up`, then `docker compose -f deploy/compose.yaml logs web worker` shows JSON log lines and no secret values.
