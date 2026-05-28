"""Serve the Vite-built dashboard frontend from the daemon at ``/``.

When the React build exists in one of the candidate dist locations,
the daemon mounts:

- ``/assets/*``    -> StaticFiles
- ``/``            -> index.html
- ``/{full_path}`` -> SPA fallback (returns index.html so client-side
                      router handles unknown paths)

When no build is found, ``/`` returns a hint JSON instead so the
operator knows what to do. ``api/*`` and ``v1/*`` paths refuse to
fall back to the SPA so a 404 surfaces a real "no such endpoint"
rather than the index HTML.

Two candidate locations are tried in priority order:

1. Installed-wheel layout (``voicegateway/_dashboard_dist``): the
   ``scripts/build_wheel.sh`` script copies the React build here at
   wheel-build time and hatchling ships it inside the wheel. This is
   what users see after ``pip install voicegateway``.
2. Editable-install / dev layout (``src/dashboard/frontend/dist``):
   the React build's natural location, produced by
   ``cd src/dashboard/frontend && npm run build``.

Always called LAST in :meth:`ApplicationBuilder._configure_routers`
so the SPA fallback at ``/{full_path}`` does not shadow ``/v1/*`` or
``/api/*``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

if TYPE_CHECKING:
    from fastapi import FastAPI

# Search order: installed-wheel layout wins over editable-install layout
# so a stale local dev build never shadows the wheel a user actually
# installed. Both Paths are computed relative to this file's location at
# ``src/voicegateway/server/static.py``.
_CANDIDATE_DIRS: list[Path] = [
    # ``src/voicegateway/_dashboard_dist`` (two parents up from this file).
    Path(__file__).resolve().parent.parent / "_dashboard_dist",
    # ``src/dashboard/frontend/dist`` (three parents up, then sideways).
    Path(__file__).resolve().parent.parent.parent / "dashboard" / "frontend" / "dist",
]


def _resolve_frontend_dir() -> Path | None:
    """Return the first candidate dir with both a tree and an ``assets/`` subdir.

    ``assets/`` is what Vite emits for hashed JS / CSS bundles; without it
    the dist is half-built and not safe to mount. First-match wins, so a
    dir present at higher priority takes precedence over later candidates.
    """
    for candidate in _CANDIDATE_DIRS:
        if candidate.exists() and (candidate / "assets").exists():
            return candidate
    return None


def mount_frontend(app: FastAPI) -> None:
    """Mount the React SPA at ``/`` (or a hint endpoint when not built)."""
    frontend_dir = _resolve_frontend_dir()
    if frontend_dir is not None:
        # Pre-resolve once so per-request path-traversal checks are
        # symbol comparisons, not stat() calls.
        frontend_root = frontend_dir.resolve()
        app.mount(
            "/assets",
            StaticFiles(directory=frontend_dir / "assets"),
            name="assets",
        )

        @app.get("/")
        async def serve_index() -> FileResponse:
            return FileResponse(frontend_dir / "index.html")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str) -> FileResponse:
            """SPA fallback: React Router handles client-side routing.

            Refuses ``api/*`` and ``v1/*`` so real 404s surface instead
            of the index HTML. Rejects any resolved path that escapes
            ``frontend_root`` (percent-encoded ``..`` segments that
            slipped past HTTP-client normalisation). Legitimate unknown
            paths still fall back to ``index.html`` so React Router can
            handle them.
            """
            if full_path.startswith(("api/", "v1/")):
                raise HTTPException(status_code=404)
            candidate = (frontend_dir / full_path).resolve()
            try:
                candidate.relative_to(frontend_root)
            except ValueError as exc:
                raise HTTPException(status_code=404) from exc
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(frontend_dir / "index.html")
    else:

        @app.get("/")
        async def missing_frontend() -> dict[str, str]:
            return {
                "error": "Frontend not built",
                "fix": "Run: cd src/dashboard/frontend && npm install && npm run build",
            }


__all__ = ["mount_frontend"]
