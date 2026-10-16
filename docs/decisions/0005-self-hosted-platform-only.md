# 0005: Self-hosted platform only

- Status: accepted; the bundled bucket is SeaweedFS (0026); the self-hosted-only scope is superseded by 0024
- Date: 2026-10-11

## Context

VoiceGateway's scope grew from cost tracking to end-to-end profiling of LiveKit voice agents: the call trace (text, per-modality metrics, costs, recordings), SFU events and resource use, and SIP events and resource use. SFU and SIP produce a steady, high-volume stream of small events, and recordings are files. The embedded single-process mode (SQLite, `voicegw serve`) cannot hold that load or that data. Supporting both an embedded mode and a platform in a 3-week launch means testing every feature twice.

## Options

1. Keep both: an embedded SQLite mode and a platform. Easiest first run, at the cost of two install shapes to build and test.
2. Platform only: one `docker compose up` starts the full stack. The SDK stays on PyPI. Heavier install, one shape to test.

## Decision

Option 2. VoiceGateway ships as a self-hosted platform of six parts: voicegateway-web, voicegateway-worker, Postgres, ClickHouse, Redis and MinIO (or any S3 bucket).

## Consequences

- The embedded SQLite mode and the in-process SQLite sink are removed. Postgres holds configuration, keys and the call index; ClickHouse holds events, metrics and turns.
- The install story becomes "pip install the SDK, docker compose up the platform", the same weight as Langfuse.
- Every feature is built and tested against the platform only.
- See 0006 (web and worker roles), 0007 (Redis streams for ingest) and 0008 (recordings).
