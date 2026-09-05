---
title: Immutable pricing and exact usage accounting
description: "Version 1 pricing revisions, decimal rating, durable usage receipts, ownership, and migration guidance"
---

VoiceGateway's version 1 accounting ledger is an opt-in companion to the
legacy request-cost records. Legacy APIs remain available; new invoice-grade
integrations should use `/v1/accounting/*`.

## Guarantees

Pricing has independent acquisition and selling revisions. A revision is
identified by tenant scope, side, revision ID, contract version, and the
SHA-256 hash of canonical JSON. Repeating identical content is idempotent.
Reusing an identity with different content returns `409`. Activation is a
separate operation and can include `expected_current_revision_id` to prevent a
lost update. Failed creation or activation does not change the previous active
revision.

`POST /v1/accounting/prepare` captures active revisions and accounting
ownership for one project, component, and offering. The resulting binding is
immutable. Provider attempts retain that binding across delayed delivery and
collector retries. A later price activation affects only later preparations.
Missing revisions produce an `unrated` event; VoiceGateway never applies the
current catalog retroactively.

Usage commits transactionally with a per-record receipt and a projection
outbox row. The SQL ledger (SQLite or PostgreSQL) is authoritative. ClickHouse
is an asynchronous, rebuildable analytics projection. Duplicate event payloads
return their original receipt, while conflicting content or a second billable
producer for the same provider attempt is durably rejected.

## Exact units and rounding

Rates and quantities are JSON decimal strings. Binary floating-point is not
used by the version 1 rater.

| Dimension | Fixed unit |
| --- | --- |
| text input/output and cache read/write | token |
| realtime audio input/output/cache | token |
| characters | character |
| audio | second |
| requests | request |

Cache quantities are subsets of text input. The rater subtracts cache read and
write from ordinary input once, then applies the cache rates. A revision must
classify every dimension as rated or unsupported. Unknown prices, missing
measurements, unsupported measurements, and explicit zero prices are distinct.

The `usd-v1-half-even-12` profile computes with 60 decimal digits and rounds
each event-side total to 12 USD fractional digits using half-even rounding.
Stored event totals are aggregated; display rounding is not accounting.
Version 1 supports USD only and rejects other currencies.

## Security and data minimization

The authenticated principal supplies the tenant. A submitted tenant identifier
is never trusted as authority. Project-restricted API keys may ingest and read
only their allowed projects. Acquisition rates, acquisition totals, and margin
are operator-only; tenant reports, dashboard responses, and the read-only MCP
status tool expose selling totals and completeness only.

The usage schema is an allowlist. It has no fields for credentials, prompts,
transcripts, caller values, arbitrary metadata, or tool arguments. Do not add
these fields to accounting extensions.

## Producer delivery

`AccountingOutbox.submit()` is the durable producer API. It writes the exact
envelope and stable event ID to a bounded SQLite outbox before returning.
`enqueue_nowait()` is intended for the voice response path: it cannot block the
call, but a process crash before the background write is a documented capture
window. A full memory queue, full disk outbox, or persistence failure increments
an explicit capture-failure signal and logs an error.

The outbox never evicts persisted, unacknowledged usage. It deletes a row only
after an `accepted` or `duplicate` durable receipt, quarantines terminal
rejections, and retains retryable or missing-receipt rows across restarts.
Operators must monitor pending count, oldest pending age, rejected count, and
capture failures. Bounded storage and an indefinitely unavailable collector
cannot mathematically guarantee zero loss; this condition is visible rather
than silent.

Delivery retries preserve event and attempt IDs. A new provider retry or
fallback receives a new attempt and event ID. SDK-owned and externally owned
accounting are selected in an operator-managed ownership assignment. Diagnostic
telemetry may still be emitted by the non-owner, but it must not be submitted
as billable usage.

## Compatibility and migration

Database migration `a6c9e2f4b817` adds the ledger and optional project allowlists
to API keys. Existing keys remain unrestricted within their tenant. Existing
request rows keep their stored floating-point totals and are labeled by their
legacy provenance; they are not re-rated or assigned invented revisions.

Rollout order:

1. Back up the SQL database and upgrade collectors.
2. Create and read back acquisition and selling revisions, verify their hashes,
   then activate each side independently.
3. Configure ownership, upgrade producers, and monitor unrated and pending
   counts side-by-side with legacy reporting.
4. Switch invoice exports only after reconciliation passes.

To roll back, stop new version 1 producers, drain or preserve their local
outboxes, and deploy the earlier application. Do not downgrade the database:
the additive tables are inert to older code and retain receipts for a later
resume. A destructive Alembic downgrade removes the ledger and is not a normal
rollback procedure.

## Minimal SDK example

```python
from voicegateway.accounting.outbox import AccountingOutbox

# Obtain an immutable preparation binding from the collector before the attempt,
# build a strict UsageEnvelope from provider measurements, then persist it.
outbox = AccountingOutbox("accounting-outbox.db", "https://collector.example")
await outbox.submit(envelope)
await outbox.drain()
```

Provider adapters must report unsupported measurements explicitly. They must
not infer absent cache, realtime-audio, reasoning, or character measurements.
