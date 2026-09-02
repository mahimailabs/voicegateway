"""Scope name constants. A leaf module: it imports nothing.

``repository`` imports these, so this file must never import from
``repository``, ``services``, ``server`` or ``schemas``. That is precisely the
cycle Wave 1 breaks: ``api_keys_repository.has_scope`` needed
``WILDCARD_SCOPE`` from ``core.auth``, while ``core.auth.verify_api_key``
needed the repository, so one of them had to import lazily inside a function
to keep the import graph acyclic. Constants live down here instead, and the
direction is one way.

The contract enum lives in ``schemas.telemetry.security_schema.ScopeName``;
``tests/core/test_scopes.py`` asserts the two agree, so a scope cannot exist
at runtime without also being visible to the authorization matrix.
"""

from __future__ import annotations

#: Telemetry ingest. Deliberately narrower than WRITE: an agent key that only
#: posts telemetry must not be able to rewrite gateway configuration.
INGEST = "ingest"
#: Reading traces, costs, sessions and replay.
READ = "read"
#: Retention, key management, the audit log, and anything that spends money.
ADMIN = "admin"
#: The read-only MCP debugging surface. Not yet enforced (VG-SEC-008, wave 3).
MCP_READ = "mcp:read"
#: Config mutation: providers, models, projects, rate-card rules. Retained,
#: but from 0.26.0 it no longer covers telemetry ingest.
WRITE = "write"
#: Matches every check. Refused at mint from 0.26.0, and satisfies nothing
#: once auth.enforcement is "enforce" (VG-SEC-006).
WILDCARD = "*"

ALL: frozenset[str] = frozenset({INGEST, READ, ADMIN, MCP_READ, WRITE, WILDCARD})

__all__ = ["ADMIN", "ALL", "INGEST", "MCP_READ", "READ", "WILDCARD", "WRITE"]
