# Phase 3: Consolidate storage onto core.Database + Alembic

**Date:** 2026-05-14
**Branch:** `feat/struc-refactor` (or a fresh branch off it)
**Status:** Approved direction (Option B). Sub-decisions tagged inline.
**Estimated blast radius:** 6 file deletions, 6 alembic versions added, 5 services and 1 facade rewired (constructor change), 1 worker relocated. Multi-commit; one commit per concern.

---

## Goal

After Phase 2, `storage/` holds a clean `ConnectionManager` + `migrator` + `schema` triple alongside the new `repository/` and `services/` layers. In parallel, `core/database.py` defines an async-SQLAlchemy `Database` plus a fully-configured Alembic environment (versions directory currently empty). The duality is paid-for: two separate connection-lifecycle systems and two parallel migration strategies for one SQLite file.

This phase consolidates onto `Database` as the single connection authority and Alembic as the single migration system, **without rewriting the 12 aiosqlite-style repositories**. Hand-tuned SQL (sessions UPSERT, latency percentiles, branding COALESCE-preserve) stays exactly as-is; only the source of the `aiosqlite.Connection` changes.

## Why now

Three forces line up:

1. **Phase 2 just shipped.** The 5 services already encapsulate connection lifecycle behind their own constructors, so retargeting them at `Database` is a one-line change per service.
2. **Migrations are already split-brain.** Numbering jumps from `(implicit baseline)` to `0003` because the early schema lives in `storage/schema.py:SCHEMA_SQL` and only later schema changes were filed as migration modules. The longer that gap sits, the more confusing it gets for anyone running `alembic history`.
3. **`core/database.py` exists, Alembic exists, and only one table (`virtual_keys`) uses them.** Either we converge or we delete the half-finished ORM stack. Convergence is cheaper than retraction.

---

## Non-goals

Listed explicitly so they don't slip in:

- **Do NOT rewrite aiosqlite repositories as SQLModel/ORM repositories.** This is Option A from brainstorming and is rejected as scope. Repos that already work continue to work; their function signatures are unchanged.
- **Do NOT remove `SQLiteStorage`.** It's the stable boundary for 41 call sites and survives this phase as pure delegation. Removal is the deferred Commit 7 from Phase 2 and warrants its own spec.
- **Do NOT introduce a separate `voicegw migrate` CLI subcommand** as a prerequisite for this phase. Migrations continue to run automatically inside `Gateway.__init__` / `SQLiteStorage._ensure_initialized`. A CLI hook can land later; it's a thin wrapper around the same code path.
- **Do NOT change `RequestRecord` from dataclass to SQLModel.** It's a write-side DTO, not a query target. No promotion happens here.
- **Do NOT change SQLite journal mode or WAL settings.** Default DELETE journaling stays. Concurrency between the SQLAlchemy pool and ad-hoc aiosqlite connections is handled by SQLite's file-level locking, which already works.

---

## Today's state

```
storage/
  __init__.py
  connection.py            ConnectionManager (path + handle tracker)
  migrator.py              initialize(db, manager): schema + 5 backfills + 5 module imports
  schema.py                SCHEMA_SQL + AUDIT_LOG_SCHEMA constants
  migrations/
    0003_turns_and_deadair.py
    0004_replay_tables.py
    0005_tenant_attribution.py
    0006_routing_and_branding.py
    0007_guardrails.py
  retention_worker.py      Periodic cleanup worker (pure orchestration)
  sqlite.py                SQLiteStorage facade (~430 LOC; takes db_path)

core/
  database.py              Database (async SQLAlchemy + SQLModel.metadata)
  container.py             DI wiring; Database singleton; VirtualKey* repo/service

alembic/
  env.py                   Configured; uses resolve_database_url(config)
  versions/                EMPTY
```

Two parallel migration concepts:

| System | Bootstrap | New schema change |
|---|---|---|
| `storage/migrator.py` | `_ensure_initialized` runs `SCHEMA_SQL` + 5 backfills + imports `migrations/*.py` | drop a new `00NN_*.py` module + register in `_MIGRATION_MODULE_PATHS` |
| Alembic | `alembic upgrade head` (never actually called) | `alembic revision --autogenerate` |

Two parallel connection-lifecycle concepts:

| Path | Used by |
|---|---|
| `ConnectionManager.connect()` → raw `aiosqlite.Connection` | every storage service + every aiosqlite repo |
| `Database.session()` → SQLAlchemy `AsyncSession` | `VirtualKeyRepository` + `VirtualKeyService` |

---

## Target shape

```
core/
  database.py
    class Database:
      session()                AsyncSession context manager (existing)
      aiosqlite_connect()      raw aiosqlite.Connection context manager (NEW)
      run_migrations()         programmatic alembic upgrade head + auto-stamp (NEW)
  container.py                 unchanged (Database is already the DI singleton)

storage/
  sqlite.py                    SQLiteStorage(Database) facade (was: SQLiteStorage(db_path))

services/
  retention_service.py         was: storage/retention_worker.py
  request_log_service.py       takes Database (was: ConnectionManager)
  cost_service.py              takes Database (was: ConnectionManager)
  latency_service.py           takes Database (was: ConnectionManager)
  session_service.py           takes Database (was: ConnectionManager)
  managed_config_service.py    takes Database (was: ConnectionManager)

repository/                    UNCHANGED (every existing file untouched)

alembic/
  versions/
    0001_baseline.py             absorbed schema.py + migrator backfills
    0002_turns_and_deadair.py    ported from storage/migrations/0003_*
    0003_replay_tables.py        ported from storage/migrations/0004_*
    0004_tenant_attribution.py   ported from storage/migrations/0005_*
    0005_routing_and_branding.py ported from storage/migrations/0006_*
    0006_guardrails.py           ported from storage/migrations/0007_*

DELETED:
  storage/connection.py
  storage/migrator.py
  storage/schema.py
  storage/migrations/  (whole directory)
```

---

## Key design choices

### 1. The aiosqlite bridge

`Database.aiosqlite_connect()` returns an async context manager that yields a freshly-opened `aiosqlite.Connection` to the same database file. It does NOT borrow from SQLAlchemy's connection pool. It opens its own connection by reusing the resolved path:

```python
@asynccontextmanager
async def aiosqlite_connect(self) -> AsyncIterator[aiosqlite.Connection]:
    """Yield a raw aiosqlite connection. For legacy repositories."""
    path = self._resolved_path
    db = await aiosqlite.connect(str(path))
    try:
        yield db
    finally:
        await db.close()
```

This sidesteps any pool entanglement: the SQLAlchemy pool and the aiosqlite connection are independent file handles. SQLite's file-level locking serializes writers; the existing code already tolerates this.

**Why not borrow from the SQLAlchemy pool?** `async_engine.raw_connection()` returns a SQLAlchemy-wrapped DBAPI connection whose `dbapi_connection` attribute is an `aiosqlite_dialect` adapter, not a plain `aiosqlite.Connection`. The methods our repos call (`db.execute`, `db.executemany`, `db.commit`, async iterators over cursors) are not 1:1 compatible. A fresh connection is cleaner.

### 2. Migration system: Alembic with programmatic upgrade

`Database.run_migrations()` invokes Alembic in-process at first initialization, then never again for that `Database` instance:

```python
async def run_migrations(self) -> None:
    if self._migrations_applied:
        return
    await asyncio.to_thread(self._run_alembic_upgrade)
    self._migrations_applied = True

def _run_alembic_upgrade(self) -> None:
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(_ALEMBIC_INI_PATH))
    cfg.set_main_option("sqlalchemy.url", resolve_database_url(self.config))
    self._stamp_legacy_db_if_needed(cfg)
    command.upgrade(cfg, "head")
```

The migration runs in a thread because Alembic's command API is synchronous. The lock is per-`Database` instance; tests creating multiple in-memory databases are unaffected.

### 3. Auto-stamping for existing databases

Existing production databases have no `alembic_version` row. Before running `command.upgrade`, `_stamp_legacy_db_if_needed` probes the schema and stamps the corresponding alembic revision:

```python
def _stamp_legacy_db_if_needed(self, cfg) -> None:
    # No alembic_version table → either fresh DB or legacy DB.
    if not _has_table(self._raw_path, "alembic_version"):
        if not _has_table(self._raw_path, "requests"):
            return  # fresh DB: alembic upgrade head builds everything
        # Legacy DB; detect schema level by feature-probing.
        stamp = _detect_schema_level(self._raw_path)
        command.stamp(cfg, stamp)
```

`_detect_schema_level` reads `PRAGMA table_info(...)` on three tables and returns the latest revision whose post-state matches:

| Feature column | Stamp revision |
|---|---|
| `guardrail_events` table exists OR `sessions.guardrails_active` | `0006_guardrails` |
| `requests.routing_*` columns OR `managed_projects.branding_json` | `0005_routing_and_branding` |
| `requests.tenant_id` | `0004_tenant_attribution` |
| `replay_storage` table exists | `0003_replay_tables` |
| `turns` table exists | `0002_turns_and_deadair` |
| `requests` table exists, none of the above | `0001_baseline` |

Each feature implies all earlier ones, so the table is read top-to-bottom and the first match wins.

### 4. Service constructor change

Every storage service today has:

```python
def __init__(self, connection_manager: ConnectionManager) -> None:
    self._conn = connection_manager

async def _connect(self) -> aiosqlite.Connection:
    return await self._conn.connect()
```

This becomes:

```python
def __init__(self, database: Database) -> None:
    self._db = database

@asynccontextmanager
async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
    async with self._db.aiosqlite_connect() as db:
        yield db
```

Service method bodies switch from `db = await self._connect(); try: ... finally: await db.close()` to `async with self._connect() as db: ...`. This is a small change per method but it's also a code-clarity win: the `try/finally` boilerplate goes away.

### 5. Migration ports

Each `storage/migrations/00NN_*.py:apply(db)` becomes an alembic version with the same effective DDL. Two important details:

**(a) Use `op.execute()` rather than `op.add_column()` where possible**, so the SQL text stays identical to today's migration body. This makes audit-by-diff trivial and means SQLite's `IF NOT EXISTS` semantics carry over unchanged.

**(b) `downgrade()` is `pass`** for every version. SQLite doesn't support `DROP COLUMN` in older versions, and our policy is forward-only migrations anyway. We document this in the spec, not as a comment in every file.

### 6. SQLiteStorage rewires through Database

```python
class SQLiteStorage:
    def __init__(self, database: Database) -> None:
        self._db = database
        self._request_log_service = RequestLogService(database)
        self._cost_service = CostService(database)
        self._latency_service = LatencyService(database)
        self._session_service = SessionService(database)
        self._managed_config_service = ManagedConfigService(database)

    async def _ensure_initialized(self) -> None:
        await self._db.run_migrations()
```

The `_ensure_initialized` body collapses to a single line because Alembic handles all the schema concerns the old migrator handled. The `aiosqlite.Connection` return is no longer needed: services manage their own connections through `Database.aiosqlite_connect()`.

`Gateway.__init__` constructs the `Database` once and passes it to `SQLiteStorage`:

```python
self._database = Database(config)
self._storage = SQLiteStorage(self._database)
```

The 41 call sites that use `gateway.storage.X(...)` see no change.

---

## Commit plan (6 commits)

Each commit ships green tests and ruff-clean. The repository remains in a working state after every commit.

### Commit 1: extend Database with aiosqlite_connect + run_migrations

**New code:**
- `core/database.py`: add `aiosqlite_connect()` async context manager
- `core/database.py`: add `run_migrations()` that calls Alembic programmatically
- `core/database.py`: add `_stamp_legacy_db_if_needed()` + `_detect_schema_level()` helpers

**Tests:**
- `tests/core/test_database_aiosqlite_bridge.py`: opens connection, runs simple INSERT/SELECT, closes
- `tests/core/test_database_run_migrations.py`: fresh DB ⇒ run_migrations creates `alembic_version` row at `head`
- `tests/core/test_database_stamping.py`: pre-built DBs at each schema level get the correct stamp before upgrade

**No consumer changes.** Existing `SQLiteStorage` still uses `ConnectionManager`; the new Database methods are unused but tested.

**Risk callouts:**
- Auto-stamping is the highest-risk piece in the whole spec. The detection logic needs explicit tests for every schema level (fresh, pre-0003, 0003, 0004, 0005, 0006, 0007/baseline-equivalent).
- Alembic programmatic invocation in CI environments where logging is reconfigured can swallow errors. Verify the test asserts on the `alembic_version` row directly, not on stdout.

### Commit 2: Alembic baseline (versions/0001_baseline.py)

**New code:**
- `alembic/versions/0001_baseline.py`: `upgrade()` runs the full post-0007 schema as raw SQL (essentially today's `SCHEMA_SQL` + the post-migrator state of `requests` after `_migrate_requests_table` + `config_audit_log` table + `managed_providers.project` column + all secondary indexes).

**Tests:**
- `tests/storage/test_alembic_baseline.py`: empty DB ⇒ `alembic upgrade 0001_baseline` ⇒ assert table list + column list + index list matches the post-0007 reality. Compare against `PRAGMA table_info` on the live test DB to keep the assertion small.

**No consumer changes.** This version is the safety net for the next commit's stamping logic.

**Risk callouts:**
- Baseline body must not diverge from what `storage/migrator.py:_migrate_requests_table` / `_migrate_managed_providers` / `_ensure_managed_table_indexes` / `_create_audit_log` produce. Generate it by inspecting a fresh DB after current migrator runs, not by hand-typing.

### Commit 3: port storage/migrations/0003-0007 to alembic versions/0002-0006

**New code:**
- `alembic/versions/0002_turns_and_deadair.py` ← `storage/migrations/0003_turns_and_deadair.py:apply()` body
- `alembic/versions/0003_replay_tables.py` ← `storage/migrations/0004_replay_tables.py:apply()` body
- `alembic/versions/0004_tenant_attribution.py` ← `storage/migrations/0005_tenant_attribution.py:apply()` body
- `alembic/versions/0005_routing_and_branding.py` ← `storage/migrations/0006_routing_and_branding.py:apply()` body
- `alembic/versions/0006_guardrails.py` ← `storage/migrations/0007_guardrails.py:apply()` body

Each version: `down_revision` chains to the prior; `upgrade()` body is the `apply()` body verbatim, switched from `await db.execute(...)` to `op.execute(...)` (synchronous; Alembic handles its own engine).

**Tests:**
- `tests/storage/test_alembic_chain.py`: build a DB at baseline, run `alembic upgrade head`, assert it reaches `0006_guardrails`. Then test each pair: pre-N → upgrade → assert post-N state.
- Keep the existing `tests/storage/test_*_migration.py` files alive as smoke tests against the new versions (they currently exercise the migrator imports; they need a one-line monkeypatch to drive Alembic instead).

**No consumer changes.** The legacy migrator path still runs in parallel; we'll cut over in Commit 4.

**Risk callouts:**
- `0007_guardrails` uses `await _table_exists(db, "managed_projects")` to guard a column-add. The port must preserve this conditional, expressed as a `bind.dialect.has_table` check inside `upgrade()`.

### Commit 4: rewire services + SQLiteStorage to Database

**Modified code:**
- `services/request_log_service.py`: `__init__(database)` and use `database.aiosqlite_connect()` context manager
- `services/cost_service.py`: same
- `services/latency_service.py`: same
- `services/session_service.py`: same
- `services/managed_config_service.py`: same
- `storage/sqlite.py`: `__init__(database)` and `_ensure_initialized` calls `database.run_migrations()` only
- `core/gateway.py`: construct `Database(config)` once, pass to `SQLiteStorage(database)`
- `tests/conftest.py`: update the storage fixture to build a `Database` and hand it to `SQLiteStorage`

**Tests:** the entire existing test suite runs; this is the cutover commit. No new tests beyond fixture updates.

**Risk callouts:**
- Test fixtures using in-memory SQLite (`":memory:"`) need a tweak: every `Database.aiosqlite_connect()` call opens a fresh connection, and `:memory:` doesn't share state across connections. Switch to `file::memory:?cache=shared` URIs OR pivot the fixture to a temp file path. Audit `tests/conftest.py` and any test that constructs `SQLiteStorage(":memory:")` directly.
- ~5 places in `tests/cli/test_cli.py` and similar may monkeypatch `SQLiteStorage._migrate_plaintext_keys` or similar internals. Audit fully before commit.

### Commit 5: move retention_worker → services/retention_service.py

**Moved code:**
- `storage/retention_worker.py` → `services/retention_service.py`
- Class name stays `RetentionWorker` (or rename to `RetentionService`; see decision below)
- Update the 1-2 call sites that import it

**Decision:** Rename to `RetentionService` for consistency with the other services. The name change is local and clarifies intent.

**Tests:** existing `tests/storage/test_retention_worker.py` (if present) moves to `tests/services/test_retention_service.py`. Imports update.

**No risk callouts.** This is a pure relocation.

### Commit 6: delete the legacy migrator + connection + schema modules

**Deleted code:**
- `storage/connection.py` (ConnectionManager)
- `storage/migrator.py`
- `storage/schema.py`
- `storage/migrations/` (whole directory, including `__init__.py`)

**Modified:**
- `storage/__init__.py`: drop any re-exports of the deleted modules
- `tests/storage/test_*_migration.py`: rebase to drive Alembic (one-line monkeypatch from Commit 3 is now permanent)

**No new tests.** This commit is purely subtractive.

**Risk callouts:**
- Any file in `tests/` that imports `voicegateway.storage.connection.ConnectionManager` or `voicegateway.storage.migrator` will break at collection time. Grep before commit; expected count is 0-2 since Commit 4 already migrated production code.
- Search for back-compat shims on `SQLiteStorage` that route to deleted modules (e.g., `_period_since` shim references `cost_repository`, which is fine; but if any shim references `voicegateway.storage.migrator._migrate_plaintext_keys`, it needs a different target).

---

## Verification per commit

After every commit:

```bash
ruff check src/ tests/
ruff format --check src/ tests/
pytest -q
```

Plus, after Commit 4:

```bash
# Manually verify schema parity against a fresh DB built the old way:
voicegw doctor --check schema
# or equivalent: compare PRAGMA table_info dumps from a pre-spec checkout
# vs the current branch
```

After Commit 6, additionally:

```bash
# Verify nothing references deleted modules
grep -rn "storage.connection\|storage.migrator\|storage.schema\|storage.migrations" \
  src/ tests/ docs/
```

Expected output: zero matches except in this design doc.

---

## Open questions and explicit decisions

These were decided during brainstorming and noted here so they don't get re-litigated:

1. **Single database file with two connection styles.** SQLite file-level locking handles concurrency. No journal-mode change.
2. **Migrations run at runtime, not via CLI.** Behavior matches today's "migrations just happen on first connect". A `voicegw migrate` CLI hook can be a one-day follow-up; it'd be a thin wrapper around `Database.run_migrations()`.
3. **Renumber to start at 0001.** The artificial gap (missing 0001/0002) was an artifact of two systems. One system, one numbering scheme.
4. **`SQLiteStorage` survives.** It's now 430 LOC of delegation and pays for itself by keeping 41 caller sites stable. Removing it is a separate spec.
5. **`models/RequestRecord` stays a dataclass.** It's a write DTO, not a query target.
6. **`downgrade()` is `pass` for every version.** SQLite doesn't support `DROP COLUMN` on older versions; our policy is forward-only.
7. **The aiosqlite bridge does not borrow from the SQLAlchemy pool.** It opens its own connection by re-resolving the path. This is intentional simplicity.

---

## Out of scope

- Rewriting any of the 12 aiosqlite-style repositories as SQLModel/ORM repos.
- Removing the `SQLiteStorage` facade (deferred Phase 2 Commit 7).
- A separate `voicegw migrate` CLI subcommand.
- Promoting `RequestRecord` to SQLModel.
- Adding new Alembic autogen workflows for the legacy tables.
- Any change to dashboard or HTTP-API behavior; this is internal-only.
