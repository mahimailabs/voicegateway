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

import os
import tempfile
from itertools import chain

import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.repository import api_keys_repository as api_keys
from voicegateway.server import build_app


class _Harness:
    """Builds an app + Gateway over a fresh SQLite db, yields a client maker."""

    def __init__(self) -> None:
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


async def _make_key(gateway, *, tenant_id=None, role="tenant"):
    """Mint a vk_ key and return its plaintext token."""
    async with gateway.storage._conn.session() as db:
        created = await api_keys.create_api_key(
            db, name=f"k-{tenant_id}-{role}", tenant_id=tenant_id, role=role
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
    if qualname == "require_principal":
        return "principal"
    return None


def live_route_auth(app) -> dict[tuple[str, str], str]:
    """Map every live ``(method, path)`` to its recovered gating."""
    found: dict[tuple[str, str], str] = {}
    for route in app.routes:
        # FastAPI can expose a compatible route subclass from a different
        # import boundary. The API contract we need is structural: a route
        # with a path, methods, and a resolved dependency graph.
        if not all(hasattr(route, field) for field in ("path", "methods", "dependant")):
            continue
        calls: list = []
        _collect_dependencies(route.dependant, calls)
        marks = sorted({m for m in (classify_dependency(f) for f in calls) if m})
        auth = "+".join(marks) if marks else "open"
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            found[(method, route.path)] = auth
    return found


def canonical_route_auth() -> dict[tuple[str, str], str]:
    """Map the route inventory the application builder includes.

    ``ApplicationBuilder`` includes these four routers directly. Inspecting
    their resolved routes avoids relying on FastAPI's app-level route list,
    which is intentionally not a stable inventory surface for this project.
    """
    from voicegateway.server.api.openorca.routes import router as openorca_router
    from voicegateway.server.routes import api_router, dashboard_router, system_router

    class _RouteInventory:
        routes = list(
            chain(
                system_router.routes,
                api_router.routes,
                dashboard_router.routes,
                openorca_router.routes,
            )
        )

    return live_route_auth(_RouteInventory())
