"""No repository reads tenant identity from the ambient ContextVar.

Six writers carried ``tenant_id if tenant_id is not None else current_tenant()``
and ``log_request`` read the ContextVar directly, so tenancy was decided in
seven places by whatever happened to be in scope. One of them,
``create_tool_calls``, also preferred a ``tenant_id`` on the row itself over
the caller's, which is VG-SEC-001: a key scoped to one tenant could write rows
tagged another.

Tenancy is now an explicit required keyword on every write. The ContextVar
stays legitimate on the agent side, where the process is the tenant and the
sink reads it at enqueue time, but it is not readable from ``repository/``.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2] / "repository"

#: Every write that stamps a tenant onto a row.
_WRITERS = [
    ("tool_calls_repository", "create_tool_calls"),
    ("turns_repository", "create_turn"),
    ("turns_repository", "create_turns_bulk"),
    ("dead_air_repository", "create_event"),
    ("transcript_turns_repository", "create_transcript_bulk"),
    ("replay_repository", "bulk_write_events"),
    ("request_log_repository", "log_request"),
]


def test_repository_never_imports_current_tenant():
    offenders = []
    for path in sorted(_REPO.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.names:
                if any(alias.name == "current_tenant" for alias in node.names):
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, offenders


@pytest.mark.parametrize(("module", "function"), _WRITERS, ids=[f for _, f in _WRITERS])
def test_every_writer_requires_an_explicit_tenant(module, function):
    """Required keyword, no default. Forgetting it is a type error, not a silent row."""
    import importlib

    fn = getattr(importlib.import_module(f"voicegateway.repository.{module}"), function)
    parameter = inspect.signature(fn).parameters.get("tenant_id")
    assert parameter is not None, f"{function} takes no tenant_id"
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, (
        f"{function} tenant_id must be keyword-only so it is never passed by position"
    )
    assert parameter.default is inspect.Parameter.empty, (
        f"{function} tenant_id has a default, so a caller can still omit it"
    )
