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

#: Everything a key may actually be minted with, which is ALL minus the
#: wildcard. Named separately because "the scopes that exist" and "the scopes
#: you may ask for" stopped being the same set in 0.26.0.
MINTABLE: frozenset[str] = ALL - {WILDCARD}


def normalize_scopes(scopes: str) -> str:
    """Validate a comma-separated scope string and return its canonical form.

    Raises :class:`ValueError` for an empty list, the wildcard, or a name that
    is not a scope. Returns the requested scopes sorted and de-duplicated, so
    two keys granting the same authority store the same string.

    This lives in the leaf module because there are two mint paths that must
    not drift: the function-style repository used by the dashboard and the
    CLI, and ``ApiKeyService.create_key`` behind ``POST /v1/api-keys``. A rule
    enforced in one of them is a rule an operator can walk around by picking
    the other door, which is how VG-SEC-007 (admin checked two ways) happened.
    """
    requested = {s.strip() for s in scopes.split(",") if s.strip()}
    if not requested:
        raise ValueError("scopes must name at least one scope")
    if WILDCARD in requested:
        raise ValueError(
            "wildcard scope may not be minted; name the scopes explicitly "
            f"(one or more of: {', '.join(sorted(MINTABLE))})"
        )
    unknown = requested - MINTABLE
    if unknown:
        raise ValueError(
            f"unknown scope(s): {sorted(unknown)}; known scopes are {sorted(MINTABLE)}"
        )
    return ",".join(sorted(requested))


__all__ = [
    "ADMIN",
    "ALL",
    "INGEST",
    "MCP_READ",
    "MINTABLE",
    "READ",
    "WILDCARD",
    "WRITE",
    "normalize_scopes",
]
