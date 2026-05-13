"""Per-entity async repositories backed by the SQLite store.

Each submodule (``dead_air``, ``replay``, ``tenants``, ``turns``, ...)
exposes a flat set of ``async def`` functions taking an
``aiosqlite.Connection`` as their first argument. The repositories share
the database file opened by :class:`voicegateway.storage.sqlite.SQLiteStorage`;
they don't manage connections themselves.
"""

__all__: list[str] = []
