"""Tests for voicegateway.server.static.mount_frontend.

The function takes two shapes depending on whether the React build
exists at ``src/dashboard/frontend/dist``:

- bundle present: mounts /assets, /, and the SPA fallback at /{path};
  the fallback refuses to serve api/* or v1/* (so a real 404 surfaces
  instead of the index HTML).
- bundle absent: serves a hint JSON at /.

Both shapes need coverage. The bundle's presence depends on whether
the developer has run ``cd src/dashboard/frontend && npm run build``,
so the tests stub the directory state with monkeypatch.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from voicegateway.server import static as static_module


def test_mount_frontend_serves_hint_when_dist_missing(monkeypatch, tmp_path):
    """``/`` returns the 'Frontend not built' hint when dist/ is absent."""
    bogus_dir = tmp_path / "does-not-exist" / "dist"
    monkeypatch.setattr(static_module, "_FRONTEND_DIR", bogus_dir)

    app = FastAPI()
    static_module.mount_frontend(app)
    client = TestClient(app)

    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] == "Frontend not built"
    assert "npm run build" in body["fix"]


def test_mount_frontend_serves_index_when_dist_present(monkeypatch, tmp_path):
    """``/`` returns index.html when dist/ + dist/assets exist."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>x</title>")
    (dist / "assets" / "main.js").write_text("console.log('hi')")
    monkeypatch.setattr(static_module, "_FRONTEND_DIR", dist)

    app = FastAPI()
    static_module.mount_frontend(app)
    client = TestClient(app)

    resp = client.get("/")
    assert resp.status_code == 200
    assert b"<!doctype html>" in resp.content


def test_mount_frontend_serves_assets_subpath(monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>")
    (dist / "assets" / "main.js").write_text("console.log('hi')")
    monkeypatch.setattr(static_module, "_FRONTEND_DIR", dist)

    app = FastAPI()
    static_module.mount_frontend(app)
    client = TestClient(app)

    resp = client.get("/assets/main.js")
    assert resp.status_code == 200
    assert b"console.log" in resp.content


def test_mount_frontend_serves_top_level_file_via_spa_fallback(monkeypatch, tmp_path):
    """A real file at dist/<path> is returned directly."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>")
    (dist / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    monkeypatch.setattr(static_module, "_FRONTEND_DIR", dist)

    app = FastAPI()
    static_module.mount_frontend(app)
    client = TestClient(app)

    resp = client.get("/favicon.ico")
    assert resp.status_code == 200
    assert resp.content.startswith(b"\x00\x00\x01\x00")


def test_mount_frontend_spa_fallback_returns_index_for_unknown_path(
    monkeypatch, tmp_path
):
    """An unknown path falls back to index.html (React Router handles it)."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>SPA-INDEX")
    monkeypatch.setattr(static_module, "_FRONTEND_DIR", dist)

    app = FastAPI()
    static_module.mount_frontend(app)
    client = TestClient(app)

    resp = client.get("/sessions/abc123")
    assert resp.status_code == 200
    assert b"SPA-INDEX" in resp.content


def test_mount_frontend_spa_fallback_refuses_api_paths(monkeypatch, tmp_path):
    """``/api/*`` and ``/v1/*`` 404 instead of falling back to the SPA index."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>SPA-INDEX")
    monkeypatch.setattr(static_module, "_FRONTEND_DIR", dist)

    app = FastAPI()
    static_module.mount_frontend(app)
    client = TestClient(app)

    assert client.get("/api/does-not-exist").status_code == 404
    assert client.get("/v1/does-not-exist").status_code == 404


def test_frontend_dir_constant_points_at_src_dashboard_frontend_dist():
    """Smoke check that the constant resolves under src/dashboard/frontend/dist."""
    expected_tail = Path("src") / "dashboard" / "frontend" / "dist"
    assert static_module._FRONTEND_DIR.as_posix().endswith(expected_tail.as_posix())
