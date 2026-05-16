"""Serve the Vite-built dashboard frontend from the daemon at ``/``.

When the React build exists at ``src/dashboard/frontend/dist/``, the
daemon mounts:

- ``/assets/*``    -> StaticFiles
- ``/``            -> index.html
- ``/{full_path}`` -> SPA fallback (returns index.html so client-side
                      router handles unknown paths)

When the build does NOT exist, ``/`` returns a hint JSON instead so
the operator knows what to do. ``api/*`` and ``v1/*`` paths refuse
to fall back to the SPA so a 404 surfaces a real "no such endpoint"
rather than the index HTML.

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

# Resolves to ``src/dashboard/frontend/dist`` from this file's location
# at ``src/voicegateway/server/static.py``. Five parents up = ``src/``.
_FRONTEND_DIR = (
    Path(__file__).resolve().parent.parent.parent / "dashboard" / "frontend" / "dist"
)


def mount_frontend(app: FastAPI) -> None:
    """Mount the React SPA at ``/`` (or a hint endpoint when not built)."""
    if _FRONTEND_DIR.exists() and (_FRONTEND_DIR / "assets").exists():
        app.mount(
            "/assets",
            StaticFiles(directory=_FRONTEND_DIR / "assets"),
            name="assets",
        )

        @app.get("/")
        async def serve_index() -> FileResponse:
            return FileResponse(_FRONTEND_DIR / "index.html")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str) -> FileResponse:
            """SPA fallback: React Router handles client-side routing."""
            if full_path.startswith(("api/", "v1/")):
                raise HTTPException(status_code=404)
            file_path = _FRONTEND_DIR / full_path
            if file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(_FRONTEND_DIR / "index.html")
    else:

        @app.get("/")
        async def missing_frontend() -> dict[str, str]:
            return {
                "error": "Frontend not built",
                "fix": "Run: cd src/dashboard/frontend && npm install && npm run build",
            }


__all__ = ["mount_frontend"]
