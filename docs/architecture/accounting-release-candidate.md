---
title: Accounting release candidate
description: "Supported boundary, compatibility, verification commands, deployment, and forward recovery for accounting v1"
---

This is the release boundary for immutable pricing and exact accounting
contract version 1. The authoritative store is SQL: SQLite for a single-node
deployment and PostgreSQL for a collector deployment. All supported accounting
write and read paths (`/v1/accounting/*`, `/api/accounting`, and
`get_accounting_status`) use that SQL ledger.

## Storage boundary

ClickHouse continues to support legacy request telemetry and cost dashboards.
An exact-accounting ClickHouse projection is not part of this release. No
supported accounting endpoint reads ClickHouse, the reserved SQL projection
table has no producer, and no `pending_delivery` field is exposed. Projection
schema, consumer, replay, deduplication, recovery, and central projection-health
reporting are deferred together so the product never reports a queue that no
worker maintains.

The producer-side SQLite outbox is different from that deferred projection. It
is supported and exposes `pending`, `rejected`, `failed_delivery`, oldest age,
memory depth, and capture failures through `AccountingOutbox.health()`.

## Compatibility matrix

| Surface | Release status | Verified combination |
| --- | --- | --- |
| Python | supported | project minimum 3.11; release tests on 3.12 |
| Embedded SQL ledger | supported | SQLite through the complete suite |
| Collector SQL ledger | supported | PostgreSQL 16 with asyncpg |
| Native LiveKit capture | supported | livekit-agents 1.5.7; package constraint `>=1.5,<1.7` |
| Pipecat accounting capture | supported through the shared sink adapter | covered by component tests; not part of the native LiveKit acceptance matrix |
| ClickHouse legacy telemetry | unchanged | existing ClickHouse tests |
| ClickHouse exact-accounting projection | unsupported/deferred | no supported accounting path depends on it |
| Exact-ledger file export | unsupported/deferred | tenant report API is the supported read path |
| MCP per-caller tenant identity | unsupported | shared token is process authority; bind one accounting tenant per process |

## Acceptance evidence

The committed gates are repeatable and use synthetic identities only:

- `test_accounting_postgres_release.py` migrates a disposable PostgreSQL,
  creates a runtime role without schema-create or ledger-delete privileges,
  and verifies immutable revisions, independent sides, concurrent activation,
  Decimal rating, concurrent deduplication, mixed receipts, project/tenant
  isolation, acquisition privacy, delayed pinned prices, restart, and lost ack.
- `test_livekit_accounting_integration.py` uses native LiveKit STT, TTS, LLM,
  and realtime base instances. It covers registration timing, cancellation,
  cache and realtime quantities, missing measurements, streaming segments,
  stable duplicate replay, ownership modes, tracing context, outage retention,
  and failed-delivery health.
- `test_release_boundary.py` installs a fail-on-use ClickHouse sentinel and
  proves the supported accounting API/dashboard reads remain SQL-only and do
  not expose `pending_delivery`. It also freezes the legacy export privacy
  boundary.
- `test_accounting_sync_example.py` runs the executable synchronization example
  against the ASGI API, including readback hash verification and delayed usage
  pinned to an older active price.

Release rehearsal commands:

```bash
uv run pytest -q src/voicegateway/tests/accounting
VOICEGW_DB_URL=postgresql+asyncpg://... \
  uv run pytest -q \
  src/voicegateway/tests/integration/test_postgres_collector.py \
  src/voicegateway/tests/integration/test_accounting_postgres_release.py
uv run --frozen pytest -q -rsxX
uv run --with 'mypy<2' --with types-PyYAML mypy
uv run --with ruff ruff check .
uv run python docs/_check_docs.py
npm --prefix src/dashboard/frontend run build
uv build
```

The final release record must attach the exact pass counts, skip/xfail
classification, built artifact name, and SHA-256 digest from the candidate run.

## Deployment and forward recovery

Deployment order:

1. Back up PostgreSQL and upgrade the collector so migration
   `a6c9e2f4b817` is applied.
2. Start with the restricted runtime role and verify `/health` plus accounting
   capability discovery.
3. Synchronize and read back acquisition and selling revisions independently;
   activate only after canonical hashes and complete dimension sets match.
4. Prepare one binding per offering, configure ownership, then roll producers
   forward. Monitor every producer outbox before using ledger totals for an
   invoice.
5. Reconcile selling totals and completeness before retiring any legacy report.

Application rollback is supported; destructive database downgrade is not.
Stop new accounting producers, preserve or drain their outboxes, deploy the
previous application, and retain the additive tables. To recover forward,
restore the candidate application and drain the same outboxes: stable event IDs
return duplicate receipts without a second row or charge. The migration's
`downgrade()` raises before changing schema so an automated rollback cannot
erase the ledger or API-key project allowlists.
