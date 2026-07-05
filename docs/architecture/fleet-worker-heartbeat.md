---
title: Fleet worker heartbeat contract
description: The single canonical contract every roster backend (the hosted cloud and the self-hosted engine console) must ingest identically, so the two worker stores cannot silently drift.
---

# Fleet worker heartbeat contract

VoiceGateway has **one** producer of worker presence and **two** stores that
consume it. This page is the canonical contract between them. Any change to how
either store ingests a heartbeat must update this page and match it, or the two
rosters silently diverge (a worker judged "online" in one and "offline" in the
other, or attributed to different tenants).

## The one producer, the two stores

- **Producer:** `voicegateway.register_worker(...)` in every agent process. It
  posts a periodic presence payload (below) to
  `${VOICEGW_COLLECTOR_URL}/v1/agents/heartbeat` with the tenant's `vk_` ingest
  key as the bearer token.
- **Store A (hosted):** `voicegateway-cloud`'s `cloud_workers` table +
  `POST /v1/agents/heartbeat` / `GET /v1/agents`, surfaced on
  `dash.voicegateway.dev`.
- **Store B (self-hosted):** the engine's own `voicegw serve` `workers` table +
  the same `/v1/agents/heartbeat` / `/v1/agents` routes, surfaced in the
  OpenOrca console via `/openorca/snapshot` and `/openorca/events`.

Because both stores implement the **same** `/v1/agents/heartbeat` contract, the
same `register_worker` heartbeat feeds either one: point `VOICEGW_COLLECTOR_URL`
at the cloud for the SaaS dashboard, or at a self-hosted `voicegw serve` for the
OpenOrca console. That symmetry only holds if both ingest identically.

## The heartbeat payload (canonical)

`register_worker`'s `presence()` sends exactly this JSON:

```json
{
  "agent_id": "worker-host-1",
  "agent_name": "myvoiceagents",
  "status": "idle",
  "active_sessions": 0,
  "version": "0.13.0",
  "project": "mahimai-realty",
  "tenant_id": null,
  "region": "iad",
  "host": "worker-host-1",
  "started_at": 1783200000.0,
  "ts": 1783200015.0
}
```

## Ingestion rules (both stores MUST follow)

1. **Tenant is derived server-side from the `vk_` key, never from the body.**
   The `tenant_id` in the payload is advisory only. A worker can only ever be
   written under the key's tenant, so it can never appear under another tenant.
2. **Identity is `(tenant, agent_id)`.** `agent_id` is the node identity for
   upsert, roster keys, and any UI node id. Do not key identity on
   `agent_name` (it groups workers, it does not identify one).
3. **`last_seen` is stamped SERVER-SIDE at ingest** (`now()` / `time.time()` on
   the receiving server). The payload `ts` is informational metadata only and
   MUST NOT drive liveness: a client clock that is skewed or forged would
   otherwise read perpetually online or offline.
4. **Upsert atomically** on `(tenant, agent_id)` via a native
   `INSERT ... ON CONFLICT DO UPDATE`. A get-then-insert races two concurrent
   first beats into duplicate rows (and a subsequent read that expects one row).
5. **Offline TTL is 45 seconds** (three missed ~15s beats). A worker whose
   server-stamped `last_seen` is older than the TTL reports `status: "offline"`
   and `active_sessions: 0`, regardless of the last status it sent.
6. **Status vocabulary is `idle | busy | offline`.** Constrain to this set on
   ingest; do not store or serve arbitrary client-supplied status strings.

## Compatibility matrix

Field / behavior, as of the two current implementations. `cloud_workers` is the
reference; the engine `workers` table (introduced with the OpenOrca console)
must align to it.

| Aspect | `cloud_workers` (cloud) | engine `workers` | Status |
|---|---|---|---|
| Primary key | `(tenant_id, agent_id)` | surrogate `id` + `UniqueConstraint(tenant_id, agent_id)` | equivalent uniqueness (OK) |
| `tenant_id` | NOT NULL (from key) | nullable (for the no-credential operator) | ⚠️ engine NULL path enables the duplicate-row race in rule 4 |
| Tenant source | key only, body ignored | key, but falls back to body `tenant_id` when the key tenant is NULL | ⚠️ align to rule 1 |
| `last_seen` | server-stamped `DateTime(tz)` | client `ts` stored as `float` | ⚠️ **primary drift** — align to rule 3 (server-stamp) |
| Upsert | native `ON CONFLICT DO UPDATE` | get-then-insert with `IntegrityError` retry | ⚠️ align to rule 4 (matches only for non-null tenants) |
| Offline TTL | 45s | 45s | OK |
| Status vocab | idle / busy / offline | idle / busy / offline (but client status passed through unvalidated) | mostly OK, add rule 6 validation |
| Node identity | `agent_id` | roster keys `agent_id`, but the OpenOrca mapper keys nodes on `agent_name` | ⚠️ align to rule 2 |

## Keeping them from drifting

- This page is the single source of truth. A PR that changes ingestion in
  either store must update this page in the same change and satisfy every rule
  above.
- Prefer sharing the semantics rather than re-deriving them: the offline TTL,
  the status vocabulary, and the payload field names should have one definition
  the engine owns (it is the producer), which the cloud consumes.
- The engine-side alignment items (rules 1-4, 6) are tracked against the
  OpenOrca console backend PR; the cloud side already satisfies the contract.
