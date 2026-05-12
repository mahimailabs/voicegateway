# Multi-tenant quickstart (v0.4.0)

VoiceGateway tags every voice session with an optional `tenant_id` so a single deployment can serve many customers and account for each one separately. This guide walks an operator through the four moves the v0.4.0 release enables:

1. Tag a session with a tenant at session-create.
2. Issue a virtual API key scoped to that tenant.
3. View per-tenant costs, metrics, and replay in the dashboard.
4. Export per-tenant data for billing or analysis.

If you only need to filter the dashboard, jump straight to [Step 3](#3-view-per-tenant-costs-and-metrics).

## Prerequisites

- VoiceGateway v0.4.0 or later (`voicegw --version` to confirm).
- The dashboard running: `voicegw dashboard` (defaults to `127.0.0.1:9090`).
- A `voicegw.yaml` with `cost_tracking.db_path` set (the dashboard reads the same SQLite database the gateway writes to).

## 1. Tag a session with a tenant

Three independent surfaces, listed in order of "least to most operator coupling." Pick whichever fits your deployment.

### Option A: pass `tenant_id` to `attach_session`

The cleanest path when your worker code knows the tenant. Refer to the [Python SDK reference](/api/python-sdk#1-attach_session-tenant_id) for the full signature.

```python
from voicegateway import inference

async def handle_call(tenant_id: str):
    agent_session = AgentSession(...)
    inference.attach_session(agent_session, tenant_id=tenant_id)
    await agent_session.start(...)
```

Use this when the LiveKit dispatcher hands your worker a context that already names the tenant (room metadata, a custom claim, a header passed through to the worker, etc.).

### Option B: `inference.set_tenant("…")`

The ContextVar escape hatch for code that does not own the `AgentSession` construction. Sets `tenant_id_ctx` for the rest of the async context; subsequent factory calls inherit the scope.

```python
from voicegateway import inference

inference.set_tenant("acme")
stt = inference.STT("deepgram/nova-3")
llm = inference.LLM("openai/gpt-4o-mini")
# Every request from this point in the async context tags 'acme'.
```

Tenant ids are bounded at 128 UTF-8 characters (locked at v0.4.0 OQ2). Unicode is allowed. Pass `None` to leave the ContextVar untouched (it does **not** clear a previously-set tenant). Use `inference.reset_tenant_id()` to clear it explicitly between sessions in long-lived tasks.

### Option C: scoped virtual API keys

When the caller is not your own agent code (a partner integration, a third-party voicebot) but you can give them a unique API key, issue a virtual key scoped to their tenant. The auth middleware auto-tags every session that arrives bearing that key. See [Step 2](#2-issue-a-virtual-api-key) for the workflow.

### Sessions without a tenant: the "unattributed" bucket

Sessions where none of the three surfaces set a tenant get `tenant_id = NULL` in storage. The dashboard renders these as a muted **unattributed** pill. The dashboard's tenant filter has a dedicated entry for the unattributed bucket so you can audit which sessions slipped through.

There is **no** automatic backfill of pre-v0.4.0 sessions. They stay `NULL` forever unless you re-tag them via the dashboard's session-detail affordance (deferred to a v0.4.x follow-up).

## 2. Issue a virtual API key

The dashboard is the only surface that issues virtual keys. The CLI is read-only by design: a CLI that printed the plaintext key would leak it via shell history and scrollback.

1. Open the dashboard at `http://127.0.0.1:9090/virtual-keys`.
2. Click **+ Issue Key**.
3. Fill in:
    - **Name** (required): a human label, e.g. `acme-prod`.
    - **Tenant scope** (optional): the tenant id you want auto-attached. Leave blank for an unscoped key.
    - **Issued by** (optional): free-form audit string.
4. Hit **Issue Key**. The next modal shows the full key **exactly once**. Copy it into your secret store before closing.

The key looks like `vk_AABBCCDDEEFFGGHHIIJJKKLLMMNNOOPP` (35 characters: `vk_` + 32 base32). The first 8 characters (`vk_AABBC`) persist as the visible prefix so you can identify a key in the list without exposing the secret; the whole key is bcrypt-hashed before storage.

Ship the key to the caller as `Authorization: Bearer vk_…`. From that point:

- Scoped keys auto-tag every session. A body-level `tenant_id` that disagrees with the key's scope returns `403`.
- Unscoped keys allow the body to declare any tenant.
- Static API keys (the v0.0.5 `auth.api_keys` block in `voicegw.yaml`) remain unchanged and never set a tenant.

### Revoke

The same Virtual Keys page exposes a **Revoke** action per row. Revocation is soft (OQ5 lock): the row stays for audit and the stale-key surface, but verification rejects further requests bearing the key within ~30 seconds.

### Stale-key detection

Keys whose `last_used_at` (or `issued_at`, for never-used keys) is older than `virtual_key_stale_days` (default 90, per-project overridable in `voicegw.yaml`) surface with a yellow **stale** badge. Hide revoked rows with the toggle at the top of the page when triaging.

## 3. View per-tenant costs and metrics

Every cost, log, sessions, metrics, and replay page in the dashboard now respects the `tenant` URL parameter.

- Use the **Tenant** typeahead in the filter strip (top-right of each page) to scope to one tenant, or pick **Unattributed** to see only sessions without attribution.
- The filter persists across navigation: switching from Costs to Sessions to Metrics keeps the same tenant in scope because the value lives in the URL.
- Selecting **All tenants** clears the filter without losing project or time-range scope.

### The Tenants tab

`GET /api/tenants` (consumed by the typeahead) returns the index of every tenant the gateway has seen, ordered by most recent activity. Each entry carries the session count, total cost, and first/last-seen timestamps. The unattributed bucket appears as a separate entry below the list.

### Per-session attribution

The Sessions page has a **Tenant** column that renders the row's `tenant_id` as a clickable pill: clicking it scopes the page to that tenant immediately. The SessionDetail modal also shows the tenant pill next to the session id so you can verify attribution without leaving the row.

## 4. Export per-tenant data

For billing exports or third-party analysis, two paths.

### CLI

```bash
voicegw tenant list --json
voicegw tenant show acme --json
```

Both commands emit JSON with the same shape `/api/tenants` returns. `tenant show` exits 1 when the tenant has no sessions so CI scripts can branch.

The `voicegw costs` command (v0.0.5) does **not** yet accept a `--tenant` flag. The dashboard's `/api/costs?tenant=…` endpoint is the canonical per-tenant cost source for v0.4.0; a CLI flag may land in v0.4.x if operators ask for it.

### Direct SQL

The `sessions` table carries `tenant_id` as of migration 0005. For ad-hoc analysis:

```sql
SELECT tenant_id,
       COUNT(*) AS session_count,
       SUM(total_cost_usd) AS total_cost
FROM sessions
WHERE started_at >= '2026-05-01'
GROUP BY tenant_id
ORDER BY total_cost DESC;
```

The `requests`, `turns`, `dead_air_events`, and `replay_*` tables all carry the column too, so any join-and-aggregate workflow you had pre-v0.4.0 can add `tenant_id` to the GROUP BY without schema gymnastics.

## What v0.4.0 does not do

A few operator workflows are deliberately deferred to keep v0.4.0 focused. Plan accordingly.

- **No automatic backfill** of pre-v0.4.0 sessions. They stay `tenant_id = NULL` (the unattributed bucket).
- **No CLI issuance of virtual keys.** REQ-VG-TENANT-003 AC-2 pins the plaintext surface to the dashboard's "show key once" modal. A CLI flow would leak via shell history.
- **No `voicegw costs --tenant`.** The dashboard's `/api/costs?tenant=…` is the canonical per-tenant cost source. CLI flag follows in a later patch if asked for.
- **No re-tag affordance for already-attributed sessions.** Once a session has a non-NULL `tenant_id`, the dashboard cannot change it; the COALESCE rule in `log_request` only fills NULL slots. A re-tag flow for unattributed sessions is on the v0.4.x roadmap.
- **Virtual keys do not carry RBAC scopes** in v0.4.0. A verified vk grants the same access a wildcard static key would. RBAC scopes on virtual keys can layer on later.

## Where the design lives

- **Refinery / Foundry**: REQ-VG-TENANT-001..004 (see the Linear project for the requirements rationale).
- **Migration**: `voicegateway/storage/migrations/0005_tenant_attribution.py`.
- **ContextVar**: `voicegateway/inference/_session_context.py`.
- **Auth middleware**: `voicegateway/server/main.py::build_app` + `voicegateway/core/auth.py`.
- **Repos**: `voicegateway/storage/virtual_keys_repo.py`, `voicegateway/storage/tenants_repo.py`.
- **Dashboard API**: `dashboard/api/main.py` (search for `/api/tenants` and `/api/virtual_keys`).
- **Frontend primitives**: `dashboard/frontend/src/components/{FilterBar,TenantFilter,TenantPill}.tsx`.
