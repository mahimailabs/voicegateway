---
title: Fleet worker heartbeat
description: The canonical heartbeat contract that every agent process produces and every roster backend (hosted cloud and self-hosted engine) must ingest identically, so the two worker stores cannot silently drift.
---
VoiceGateway has one producer of worker presence and two stores that consume it. This page is the canonical contract between them. Any change to how either store ingests a heartbeat must update this page and match it, or the two rosters silently diverge (a worker judged "online" in one and "offline" in the other, or attributed to different tenants).

## The one producer, the two stores

- **Producer:** `voicegateway.register_worker(...)` in every agent process. It posts a periodic presence payload to `${VOICEGW_COLLECTOR_URL}/v1/agents/heartbeat` with the tenant's `vk_` ingest key as the bearer token.
- **Store A (hosted):** `voicegateway-cloud`'s `cloud_workers` table and `POST /v1/agents/heartbeat` / `GET /v1/agents`, surfaced on `dash.voicegateway.dev`.
- **Store B (self-hosted):** the engine's own `voicegw serve` `workers` table and the same `/v1/agents/heartbeat` / `/v1/agents` routes, surfaced in the OpenOrca console via `/openorca/snapshot` and `/openorca/events`.

Because both stores implement the same `/v1/agents/heartbeat` contract, the same `register_worker` heartbeat feeds either one. Point `VOICEGW_COLLECTOR_URL` at the cloud for the SaaS dashboard, or at a self-hosted `voicegw serve` for the OpenOrca console. That symmetry only holds if both ingest identically.

## Agent environment variables

Each agent process needs three environment variables to participate in the fleet roster:

| Variable | Purpose |
|---|---|
| `VOICEGW_COLLECTOR_URL` | Base URL of the heartbeat endpoint (e.g. `https://api.voicegateway.dev`) |
| `VOICEGW_API_KEY` | The `vk_` ingest key that authenticates the agent and determines its tenant |
| `VOICEGW_AGENT_ID` | Stable node identity for this worker process (e.g. `worker-host-1`) |

## The heartbeat payload (canonical)

`register_worker`'s `presence()` sends exactly this JSON:

```json
{
  "agent_id": "worker-host-1",
  "agent_name": "myvoiceagents",
  "dispatch_name": "myvoiceagents",
  "status": "idle",
  "active_sessions": 0,
  "version": "0.22.3",
  "project": "mahimai-realty",
  "tenant_id": null,
  "region": "iad",
  "host": "worker-host-1",
  "started_at": 1783200000.0,
  "memory_rss_bytes": 184320000,
  "memory_total_bytes": 8589934592,
  "cpu_pct": 3.2,
  "ts": 1783200015.0
}
```

`dispatch_name` is the LiveKit `agent_name` this worker dispatches under (defaults to `agent_name`; `None` for a worker with no LiveKit dispatch, e.g. Pipecat). The dashboard's play-button probe dispatches by this field; it is not part of the `(tenant, agent_id)` identity key.

## Ingestion rules (both stores must follow)

1. **Tenant is derived server-side from the `vk_` key, never from the body.** The `tenant_id` in the payload is advisory only. A worker can only ever be written under the key's tenant, so it can never appear under another tenant.
2. **Identity is `(tenant, agent_id)`.** `agent_id` is the node identity for upsert, roster keys, and any UI node id. Do not key identity on `agent_name` (it groups workers, it does not identify one).
3. **`last_seen` is stamped server-side at ingest** (`now()` / `time.time()` on the receiving server). The payload `ts` is informational metadata only and must not drive liveness: a client clock that is skewed or forged would otherwise read perpetually online or offline.
4. **Upsert atomically** on `(tenant, agent_id)`; a naive get-then-insert races two concurrent first beats into duplicate rows. A native `ON CONFLICT DO UPDATE` works when tenant is never NULL (key-authenticated writes). When tenant can be NULL (the self-hosted, no-credential operator), `NULL != NULL` under the unique constraint breaks `ON CONFLICT`, so select first, update or insert, and retry on `IntegrityError` from a concurrent first insert.
5. **Offline TTL is 45 seconds** (three missed ~15s beats). A worker whose server-stamped `last_seen` is older than the TTL reports `status: "offline"` and `active_sessions: 0`, regardless of the last status it sent.
6. **Status vocabulary is `idle | busy | offline`.** Constrain to this set on ingest; do not store or serve arbitrary client-supplied status strings.

## Compatibility matrix

Field or behavior as of the two current implementations. `cloud_workers` is the reference; the engine `workers` table (introduced with the OpenOrca console) must satisfy the same rules, though not always with the same mechanism.

| Aspect | `cloud_workers` (cloud) | Engine `workers` | Status |
|---|---|---|---|
| Primary key | `(tenant_id, agent_id)` | Surrogate `id` + `UniqueConstraint(tenant_id, agent_id)` | Equivalent uniqueness (OK) |
| `tenant_id` | NOT NULL (from key) | Nullable (for the no-credential operator) | Structural difference; it's why the engine can't use a plain `ON CONFLICT` (see the Upsert row) |
| Tenant source | Key only, body ignored | Key, but falls back to body `tenant_id` when the key tenant is NULL | Align to rule 1 |
| `last_seen` | Server-stamped `DateTime(tz)` | Client `ts` stored as `float` | Primary drift: align to rule 3 (server-stamp) |
| Upsert | Native `ON CONFLICT DO UPDATE` (tenant always non-null) | Select-then-update, `IntegrityError` retry on races | Both satisfy rule 4; the engine can't use `ON CONFLICT` because `tenant_id` is nullable |
| Offline TTL | 45s | 45s | OK |
| Status vocab | idle / busy / offline | idle / busy / offline (but client status passed through unvalidated) | Add rule 6 validation |
| Node identity | `agent_id` | Roster keys `agent_id`, but the OpenOrca mapper keys nodes on `agent_name` | Align to rule 2 |

<Warning>
The most impactful drift today is `last_seen` source. The engine stores the client-supplied `ts` float instead of stamping server-side. A worker with a skewed clock appears permanently online or offline in the OpenOrca console even after the process exits.
</Warning>

## Keeping them from drifting

- This page is the single source of truth. A PR that changes ingestion in either store must update this page in the same change and satisfy every rule above.
- Prefer sharing semantics rather than re-deriving them: the offline TTL, the status vocabulary, and the payload field names should have one definition the engine owns (it is the producer), which the cloud consumes.
- The engine-side alignment items (rules 1, 2, 3, 6) are tracked against the OpenOrca console backend PR; the cloud side already satisfies the contract. Rule 4 (atomic upsert) is already satisfied by both, just via different mechanisms (see the compatibility matrix).

## Related pages

<CardGroup cols={2}>
  <Card title="Security model" href="/architecture/security">
    Tenant isolation via vk_ ingest keys and server-side stamping.
  </Card>
  <Card title="Hosted cloud quickstart" href="/hosted/quickstart">
    Setting VOICEGW_COLLECTOR_URL, VOICEGW_API_KEY, and VOICEGW_AGENT_ID.
  </Card>
  <Card title="Guide: attach" href="/guide/attach">
    Per-call attach for sub-tenant cost attribution.
  </Card>
  <Card title="Environment variables" href="/configuration/environment-variables">
    Full reference for all fleet-related env vars.
  </Card>
</CardGroup>
