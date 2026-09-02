"""Shared app harness for the Wave 0 telemetry security contract tests.

``_Harness`` and ``_make_key`` are copied from
``test_read_tenant_isolation.py`` rather than imported. Importing a private
name across test modules couples two files that are meant to be independently
readable, and the original is itself a test fixture rather than a helper API.

The copy preserves the one load-bearing detail: ``VOICEGW_DB_PATH`` is saved
and restored in :meth:`_Harness.cleanup`. That env var wins over the config
``db_path`` in ``core.database``, so leaking it would redirect every later
test's SQLite engine at this harness's deleted temp file. That is why this
harness is safe to run alongside the rest of the suite.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterator

import yaml
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.repository import api_keys_repository as api_keys
from voicegateway.server import build_app


class _Harness:
    """Builds an app + Gateway over a fresh SQLite db, yields a client maker."""

    def __init__(self, config_overrides: dict | None = None) -> None:
        """Build the app. ``config_overrides`` merges into the yaml top level.

        Used to vary the ``auth`` block per test (enforcement mode, local
        development, configured keys) without a second harness.
        """
        self._tmp = tempfile.TemporaryDirectory()
        tmp = self._tmp.name
        # See the module docstring: restoring this is what keeps the harness
        # from redirecting the rest of the suite at a deleted file.
        self._prev_db_path = os.environ.get("VOICEGW_DB_PATH")
        os.environ["VOICEGW_DB_PATH"] = os.path.join(tmp, "telemetry_contract.db")
        cfg = {
            "providers": {"openai": {"api_key": "test-key"}},
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "projects": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": True},
        }
        if config_overrides:
            cfg.update(config_overrides)
        cfg_path = os.path.join(tmp, "voicegw.yaml")
        with open(cfg_path, "w") as handle:
            yaml.dump(cfg, handle)
        try:
            self.gateway = Gateway(config_path=cfg_path)
            self.app = build_app(
                self.gateway, enable_mcp_sse=False, enable_dashboard=False
            )
            # SQLite path: no ClickHouse client bound.
            self.app.state.ch_client = None
        except Exception:
            self.cleanup()
            raise

    def client(self) -> AsyncClient:
        """Return an ASGI client bound to this harness's app."""
        return AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://test"
        )

    def cleanup(self) -> None:
        """Restore ``VOICEGW_DB_PATH`` and delete the temp directory."""
        if self._prev_db_path is None:
            os.environ.pop("VOICEGW_DB_PATH", None)
        else:
            os.environ["VOICEGW_DB_PATH"] = self._prev_db_path
        self._tmp.cleanup()


async def _make_key(gateway, *, tenant_id=None, role="tenant", scopes="read"):
    """Mint a vk_ key and return its plaintext token.

    ``scopes`` is explicit because 0.26.0 stops minting wildcard keys: a test
    that wants to write must say so, exactly as an operator now must. The
    default is ``read`` rather than the old ``*`` so that a test which needs
    write authority fails loudly instead of passing on a scope that matched
    everything.
    """
    async with gateway.storage.session() as db:
        created = await api_keys.create_api_key(
            db,
            name=f"k-{tenant_id}-{role}-{scopes}",
            tenant_id=tenant_id,
            role=role,
            scopes=scopes,
        )
    return created.plaintext


# ---------------------------------------------------------------------------
# Route introspection
# ---------------------------------------------------------------------------
#
# Deliberately duplicated from tools/scripts/gen_authorization_matrix.py. The
# generator lives outside src/ so it is neither linted nor type-checked, and a
# test must not depend on an unchecked file for its own correctness. The one
# assumption the two share (require_scope's closure shape) is asserted
# directly by test_telemetry_security_contract.py, so a refactor that breaks
# it fails loudly in one obvious place rather than silently mislabelling rows.

_SCOPE_QUALNAME = "require_scope.<locals>._dep"

#: ``{name:converter}`` -> ``{name}``, matching OpenAPI's normalisation.
_CONVERTER_RE = re.compile(r":[^{}]+\}")


def _collect_dependencies(dependant, out: list) -> None:
    """Collect every callable in the resolved dependency tree."""
    for sub in dependant.dependencies:
        if sub.call is not None:
            out.append(sub.call)
        _collect_dependencies(sub, out)


def classify_dependency(fn) -> str | None:
    """Return the ``RouteAuth`` value a dependency implies, or ``None``."""
    qualname = getattr(fn, "__qualname__", "")
    if qualname == _SCOPE_QUALNAME:
        code = fn.__code__
        assert code.co_freevars == ("scope",), (
            "require_scope's closure no longer exposes exactly one free "
            f"variable named 'scope' (found {code.co_freevars!r}); the "
            "authorization matrix cannot be classified until this is updated"
        )
        cell = fn.__closure__[code.co_freevars.index("scope")]
        return f"scope:{cell.cell_contents}"
    if qualname == "require_ingest_principal":
        return "scope:ingest"
    if qualname == "require_principal":
        return "principal"
    return None


def iter_api_routes(routes) -> Iterator[tuple[str, APIRoute]]:
    """Yield ``(resolved_path, APIRoute)``, flattening FastAPI's wrappers.

    FastAPI changed how ``include_router`` stores routes, and this project's
    CI runs both shapes, so both have to work:

    - **<= 0.136** copies each included route onto the parent as a real
      ``APIRoute`` with its full path already resolved.
    - **0.141** stores one lazy ``_IncludedRouter`` per inclusion instead. It
      is not an ``APIRoute``, and it has no ``path``, ``methods`` or
      ``dependant``, so a check for any of those skips it and everything
      underneath it. The resolved routes come from
      ``effective_route_contexts()``, whose entries carry the fully-prefixed
      ``path`` alongside the ``original_route``.

    This is why Test Coverage failed while the test matrix passed: the two
    jobs resolved different FastAPI versions, and under 0.141 the inventory
    collapsed to the four routes declared by decorator rather than by
    inclusion. A route this function fails to yield vanishes from the
    authorization matrix, so breadth here is deliberate.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route.path, route
        elif hasattr(route, "effective_route_contexts"):
            for context in route.effective_route_contexts():
                original = getattr(context, "original_route", None)
                if isinstance(original, APIRoute):
                    yield context.path, original
        elif hasattr(route, "routes"):
            yield from iter_api_routes(route.routes)


def live_route_auth(app) -> dict[tuple[str, str], str]:
    """Map every live ``(method, path)`` to its recovered gating."""
    found: dict[tuple[str, str], str] = {}
    for path, route in iter_api_routes(app.routes):
        calls: list = []
        _collect_dependencies(route.dependant, calls)
        marks = sorted({m for m in (classify_dependency(f) for f in calls) if m})
        auth = "+".join(marks) if marks else "open"
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            found[(method, path)] = auth
    return found


def normalize_route_key(key: tuple[str, str]) -> tuple[str, str]:
    """Strip a path converter so a route key can be compared to OpenAPI."""
    method, path = key
    return (method, _CONVERTER_RE.sub("}", path))


def openapi_route_keys(app) -> set[tuple[str, str]]:
    """Return ``(method, path)`` for every route in the OpenAPI schema.

    A version-stable second opinion on :func:`iter_api_routes`. The schema is
    public API and survives the internal restructuring above, so comparing the
    two catches the next such change as a named failure rather than as a
    silently short inventory. Path converters are stripped because the schema
    normalises ``{id:path}`` to ``{id}``.
    """
    schema = app.openapi()
    return {
        (method.upper(), _CONVERTER_RE.sub("}", path))
        for path, operations in schema.get("paths", {}).items()
        for method in operations
        if method.upper() not in {"HEAD", "OPTIONS"}
    }


def canonical_route_auth() -> dict[tuple[str, str], str]:
    """Map the route inventory the application builder includes.

    The route aggregators are module-level singletons. Other test modules may
    reconfigure them, so the inventory is taken in a clean child interpreter
    rather than trusting the parent test process's mutable state.

    The child reads the four routers ApplicationBuilder registers. Its
    app-level ``routes`` inventory is not portable: on Linux under coverage it
    can be empty despite those routers containing every endpoint. Main attaches
    no dependencies at ``include_router`` time, so these resolved router routes
    are the production inventory today. If that changes, make the dependency
    explicit on the relevant router rather than relying on app-level state.
    """
    script = """
import json
from itertools import chain

from voicegateway.server.api.openorca.routes import router as openorca_router
from voicegateway.server.routes import api_router, dashboard_router, system_router
from voicegateway.tests.server._telemetry_harness import live_route_auth

inventory = type(
    "_RouteInventory",
    (),
    {
        "routes": list(
            chain(
                system_router.routes,
                api_router.routes,
                dashboard_router.routes,
                openorca_router.routes,
            )
        )
    },
)()
auth = live_route_auth(inventory)
print(json.dumps([[method, path, value] for (method, path), value in auth.items()]))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        # check=True would raise CalledProcessError with the child's stderr
        # captured and unshown, at collection time, which reads as "the module
        # failed to import" and hides the cause. Surface it instead.
        raise RuntimeError(
            "route inventory child process failed "
            f"(exit {result.returncode}):\n{result.stderr.strip()}"
        )
    rows = json.loads(result.stdout)
    return {(method, path): auth for method, path, auth in rows}
