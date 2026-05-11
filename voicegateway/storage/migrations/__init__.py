"""Versioned SQLite migrations.

Each migration is a numbered module exporting an ``apply(db)`` coroutine.
Migrations are applied in numeric order by ``voicegateway.storage.sqlite``'s
init path (wired in v0.2.0's T07). Each migration must be idempotent so
re-runs against an already-migrated database are no-ops; the canonical
guard is ``PRAGMA table_info`` against the target table.

This package re-exports nothing at the top level; ``__all__`` is empty by
design (REQ-VG-POLISH-006 AC-1). Migrations are imported by file path.
"""

__all__: list[str] = []
