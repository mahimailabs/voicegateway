# Phase 2: Decompose storage/sqlite.py into the layered architecture

**Date:** 2026-05-13
**Branch:** `feat/struc-refactor` (or a fresh branch off it)
**Status:** Approved as direction; sub-decisions tagged below
**Estimated blast radius:** 6 new service files + 6 new repository files + ~40 call-site updates. Multi-commit; recommend one commit per concern.

---

## Goal

`src/voicegateway/storage/sqlite.py` is 1,721 lines and acts as a god-class
covering schema, migrations, request logging, cost aggregation, latency
aggregation, session lifecycle, and managed-config CRUD. This spec
decomposes it into the layered shape the rest of the codebase already
follows: per-entity repositories under `repository/`, per-concern services
under `services/`, and the SQLite backend itself reduced to a connection
manager.

## Anatomy of today's `sqlite.py`

| Concern | LOC (approx) | Public methods |
|---|---|---|
| Schema + migrations | 300 | `_ensure_initialized`, `_migrate_plaintext_keys`, schema constants |
| Request log | 200 | `log_request`, `get_recent_requests` |
| Cost aggregation | 250 | `get_cost_summary`, `get_cost_by_project` |
| Latency aggregation | 250 | `get_latency_stats` (+ percentile CTEs) |
| Session lifecycle | 300 | `get_session_by_id`, `list_sessions`, `finalize_session_metrics`, `finalize_session_replay_storage` |
| Managed-config CRUD | 400 | `upsert_managed_provider`, `upsert_managed_model`, `upsert_managed_project`, `rotate_managed_credentials`, `_validate_branding` |

## Target shape after decomposition

```
src/voicegateway/
├── repository/
│   ├── base_repository.py               (existing)
│   ├── request_log_repository.py        ← new (insert + recent-list)
│   ├── cost_repository.py               ← new (aggregation queries)
│   ├── latency_repository.py            ← new (percentile + window queries)
│   ├── session_repository.py            ← new (sessions table)
│   ├── managed_provider_repository.py   ← new (managed_providers table)
│   ├── managed_model_repository.py      ← new (managed_models table)
│   └── managed_project_repository.py    ← new (managed_projects table)
├── services/
│   ├── request_log_service.py           ← new (orchestrates log_request)
│   ├── cost_service.py                  ← new (period filters, project scoping)
│   ├── latency_service.py               ← new (percentile selection)
│   ├── session_service.py               ← new (finalize_metrics + replay_size)
│   └── managed_config_service.py        ← new (umbrella over the three managed_* repos)
├── storage/
│   ├── __init__.py
│   ├── connection.py                    ← new (was the aiosqlite-management
│   │                                       part of SQLiteStorage)
│   ├── schema.py                        ← new (the CREATE TABLE constants)
│   ├── migrator.py                      ← new (was _ensure_initialized +
│   │                                       _migrate_plaintext_keys)
│   ├── retention_worker.py              (existing, lightly updated)
│   └── migrations/                      (existing, unchanged)
└── models/
    ├── managed_provider_model.py        ← new (dataclass mirror of the row)
    ├── managed_model_model.py           ← new
    └── managed_project_model.py         ← new
```

## Boundary contract

| Layer | Owns | Doesn't own |
|---|---|---|
| `storage/connection.py` | Opening aiosqlite connections, returning a `Connection`. | No business logic, no SQL beyond schema/migrations. |
| `storage/schema.py` | `CREATE TABLE` / `CREATE INDEX` / `CREATE VIEW` constants. | Migrations (those are versioned modules). |
| `storage/migrator.py` | Running migrations in order. Idempotent. | Per-entity queries. |
| `repository/<entity>_repository.py` | Raw async SQL against the entity's table. Returns dataclasses / lists / dicts. | Period filters, business rules, validation. |
| `services/<concern>_service.py` | Period filters, project scoping, validation, multi-repo orchestration. | aiosqlite connection lifecycle (the service receives a `Database` and asks for a session). |
| `models/<entity>_model.py` | The row dataclass. | SQL, business logic. |

## What happens to the `SQLiteStorage` class

**Decision required at implementation start.** Pick one:

### Option A — Delete entirely, callers go through services

`gateway.storage.get_cost_summary(...)` → `gateway.cost_service.get_summary(...)`.

- Pros: clean. No facade. The decomposition is real.
- Cons: every call site updates (~30 sites). Tests fixture `storage = SQLiteStorage(...)` pattern needs rewriting.

### Option B — Keep as a transitional facade (recommended)

`SQLiteStorage` retains its public method names but delegates to services
internally. Each method gets a one-line `warnings.warn(DeprecationWarning,
"Call CostService.get_summary instead of SQLiteStorage.get_cost_summary")`.

- Pros: zero call-site changes during the decomposition. Old tests keep
  passing. Deprecation warnings nudge migrations.
- Cons: god-class lives on as a facade until a final cleanup commit
  removes it. Two paths to the same data during the window.

**Recommendation:** Option B for the decomposition, then a follow-up
commit that deletes the facade once all 30 callers have moved.

## Execution plan (per-concern commits)

The decomposition is too big for one commit. Each concern gets its own:

### Commit 1 — Scaffolding

- Create `storage/{connection,schema,migrator}.py` with extracted code.
- Update `core/database.py` (the SQLAlchemy engine) to coexist with the
  new SQLite connection helper; both target the same file.
- Run tests. Expect green.

### Commit 2 — Request log

- Extract `log_request` and `get_recent_requests` into
  `repository/request_log_repository.py` (function-based, async, takes
  `aiosqlite.Connection`).
- Add `services/request_log_service.py` that takes a `Database` and
  wraps the repo.
- Wire into the DI container: `request_log_repository`,
  `request_log_service`.
- If using Option B, route `SQLiteStorage.log_request` through the
  service with a deprecation warning. Otherwise update callers.

### Commit 3 — Cost aggregation

Same pattern. Extract `get_cost_summary`, `get_cost_by_project` into
`repository/cost_repository.py` + `services/cost_service.py`. CTEs and
date filters stay as raw SQL via `connection.execute(text(...))` — the
ORM tax on these queries is unwarranted.

### Commit 4 — Latency aggregation

Same pattern. Percentile computation helper stays in
`utils/percentiles.py`; SQL lives in `repository/latency_repository.py`.

### Commit 5 — Session lifecycle

Same pattern. `session_service.py` owns `finalize_session_metrics` and
`finalize_session_replay_storage` since those orchestrate across the
turns + dead_air + replay + sessions tables.

### Commit 6 — Managed config

Three repos (providers / models / projects) under one service umbrella
because the three tables share validation rules (`_validate_branding`)
and the credential-rotation flow spans `managed_providers`. Add the
three model dataclasses to `models/`.

### Commit 7 — Cleanup (only if Option B was used)

Delete the `SQLiteStorage` facade. Update the remaining call sites.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Each concern depends on others' helpers (e.g., percentile compute) | Extract shared helpers to `utils/` first (already done for `percentiles.py`). |
| Tests use `SQLiteStorage` fixtures directly | Option B (facade) sidesteps this entirely. |
| The schema migration ordering (0003–0007) is touched | Don't change migrations themselves; only the runner. The schema source of truth stays the existing files. |
| The transition window has two code paths for every query | Bound the window: target one concern per commit, finish all six before deleting the facade. |
| Aggregation tests have hand-written expected values | Run the test suite after each commit. Don't refactor query SQL during the decomposition; only relocate it. |

## Decisions deferred to the implementing session

When `/goal` fires this spec, the implementing session must resolve:

1. **Facade-vs-delete choice.** Recommendation: facade (Option B above).
2. **Granularity of `managed_config_service.py`.** One umbrella service vs three (one per entity). Recommendation: umbrella, because credential rotation crosses entities.
3. **Whether to also migrate to SQLAlchemy ORM in this pass.** Recommendation: **no.** The decomposition is its own change; the ORM migration (for the `requests` and `sessions` tables specifically) is a third spec when this lands. Keep the raw aiosqlite SQL in the new repos; preserve the hand-tuned CTEs.
4. **Whether to expose new methods through the DI container's wiring_config**. Recommendation: yes, but only the modules that have FastAPI routers using `@inject`. The CLI doesn't use `@inject`.

## Verification checklist (per commit)

- [ ] `python -m pytest -q` is green.
- [ ] `ruff check src/voicegateway src/dashboard` is clean.
- [ ] `SQLiteStorage._ensure_initialized()` still creates the same schema
      (run a smoke test: bring up storage on a tmp file, dump `.schema`,
      compare against the pre-decomposition snapshot).
- [ ] If using Option B: every method on `SQLiteStorage` either delegates
      to a service or stays as-is. No method silently does both.
- [ ] Subpackage-count contract in `tests/integration/test_public_api.py`
      tracks the actual count.

## Out of scope (for this spec)

- Migrating the `requests` and `sessions` tables to SQLModel ORM. That's
  a follow-on spec; the decomposition done here puts the seams in the
  right place so the ORM migration becomes a smaller change.
- Alembic baseline migration that captures the existing schema. Manual
  migrations 0003–0007 stay as the source of truth.
- Removing `core/auth.py`'s direct use of the legacy
  `virtual_keys_repository` function API.
