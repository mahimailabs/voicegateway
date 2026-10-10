# 0004: One combined FastAPI server

- Status: accepted
- Date: 2026-05 (recorded 2026-10-10)

## Context

The dashboard used to run its own FastAPI process (`src/dashboard/api/main.py`) beside the `/v1` API. Two processes meant two ports, two auth paths and two deploy units for a tool meant to be self-hosted on one machine.

## Options

1. Keep two processes: separation of concerns, at the cost of setup and drift between auth paths.
2. One app: `voicegw serve` mounts `/health`, `/v1/*`, the dashboard router at `/api/*` and the built SPA at `/`.

## Decision

Option 2. The dashboard routes moved to `server/api/dashboard/`; they did not go away.

## Consequences

- One port and one container for API, dashboard and SPA.
- Dashboard reads live under `/api/*` behind `require_principal`; writes and ingest stay under `/v1/*`.
- `src/dashboard/api/` no longer holds any Python.
