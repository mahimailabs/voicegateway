"""Tests for voicegateway.server.static.mount_frontend.

The function takes two shapes depending on whether a React build is
discovered in one of the two candidate locations:

- bundle present: mounts /assets, /, and the SPA fallback at /{path};
  the fallback refuses to serve api/* or v1/* (so a real 404 surfaces
  instead of the index HTML).
- bundle absent: serves a hint JSON at /.

Two candidate locations are tried in priority order: installed-wheel
layout (``voicegateway/_dashboard_dist``, populated by
``scripts/build_wheel.sh``) wins over editable-install layout
(``src/dashboard/frontend/dist``, populated by ``npm run build``).
The tests stub ``_CANDIDATE_DIRS`` with monkeypatch so each case
isolates its own filesystem state.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from voicegateway.server import static as static_module


def _patch_candidates(monkeypatch, dirs: Iterable[Path]) -> None:
    """Replace ``_CANDIDATE_DIRS`` with the given dirs for one test."""
    monkeypatch.setattr(static_module, "_CANDIDATE_DIRS", list(dirs))


def _make_dist(root: Path, *, with_assets: bool = True, marker: str = "x") -> Path:
    """Create a minimal Vite-style dist at ``root`` and return the dir."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(f"<!doctype html><title>{marker}</title>")
    if with_assets:
        (root / "assets").mkdir(parents=True, exist_ok=True)
        (root / "assets" / "main.js").write_text(
            f"console.log('{marker}')",
        )
    return root


def test_mount_frontend_serves_hint_when_dist_missing(monkeypatch, tmp_path):
    """``/`` returns the 'Frontend not built' hint when no candidate exists."""
    bogus_dir = tmp_path / "does-not-exist" / "dist"
    _patch_candidates(monkeypatch, [bogus_dir])

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
    dist = _make_dist(tmp_path / "dist")
    _patch_candidates(monkeypatch, [dist])

    app = FastAPI()
    static_module.mount_frontend(app)
    client = TestClient(app)

    resp = client.get("/")
    assert resp.status_code == 200
    assert b"<!doctype html>" in resp.content


def test_mount_frontend_serves_assets_subpath(monkeypatch, tmp_path):
    dist = _make_dist(tmp_path / "dist")
    _patch_candidates(monkeypatch, [dist])

    app = FastAPI()
    static_module.mount_frontend(app)
    client = TestClient(app)

    resp = client.get("/assets/main.js")
    assert resp.status_code == 200
    assert b"console.log" in resp.content


def test_mount_frontend_serves_top_level_file_via_spa_fallback(monkeypatch, tmp_path):
    """A real file at dist/<path> is returned directly."""
    dist = _make_dist(tmp_path / "dist")
    (dist / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    _patch_candidates(monkeypatch, [dist])

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
    dist = _make_dist(tmp_path / "dist", marker="SPA-INDEX")
    _patch_candidates(monkeypatch, [dist])

    app = FastAPI()
    static_module.mount_frontend(app)
    client = TestClient(app)

    resp = client.get("/sessions/abc123")
    assert resp.status_code == 200
    assert b"SPA-INDEX" in resp.content


def test_mount_frontend_spa_fallback_refuses_api_paths(monkeypatch, tmp_path):
    """``/api`` and ``/v1`` (bare or prefixed) 404 rather than serving the SPA."""
    dist = _make_dist(tmp_path / "dist", marker="SPA-INDEX")
    _patch_candidates(monkeypatch, [dist])

    app = FastAPI()
    static_module.mount_frontend(app)
    client = TestClient(app)

    assert client.get("/api/does-not-exist").status_code == 404
    assert client.get("/v1/does-not-exist").status_code == 404
    # Bare namespace roots must 404 too, not fall back to index.html.
    assert client.get("/api").status_code == 404
    assert client.get("/v1").status_code == 404


def test_mount_frontend_spa_fallback_refuses_path_traversal(monkeypatch, tmp_path):
    """Resolved paths that escape ``frontend_dir`` must 404, not leak.

    HTTP clients and Starlette normally normalise ``../`` in URLs before
    the handler sees them, but percent-encoded forms (``%2E%2E%2F``)
    slip past normalisation and reach the handler with literal ``..``
    segments intact. Without the resolved-path check in spa_fallback,
    `(frontend_dir / full_path).is_file()` would happily stat files
    outside the dist root.
    """
    dist = _make_dist(tmp_path / "wheel" / "_dashboard_dist", marker="SAFE")
    secret = tmp_path / "should-not-leak.txt"
    secret.write_text("LEAKED")
    _patch_candidates(monkeypatch, [dist])

    app = FastAPI()
    static_module.mount_frontend(app)
    client = TestClient(app)

    resp = client.get("/%2E%2E/should-not-leak.txt")
    assert b"LEAKED" not in resp.content, (
        "path-traversal guard failed; secret file leaked through spa_fallback"
    )


# ---------- two-path resolution (spec section 4) ---------------------------


def test_installed_wheel_layout_wins_when_present(monkeypatch, tmp_path):
    """Wheel-layout candidate at priority 0 wins over the editable-install one.

    Both candidates have valid dists with distinct markers; the request to
    ``/`` must return the wheel-layout marker.
    """
    wheel_dist = _make_dist(tmp_path / "wheel" / "_dashboard_dist", marker="WHEEL")
    editable_dist = _make_dist(
        tmp_path / "editable" / "dashboard" / "frontend" / "dist",
        marker="EDITABLE",
    )
    _patch_candidates(monkeypatch, [wheel_dist, editable_dist])

    app = FastAPI()
    static_module.mount_frontend(app)
    client = TestClient(app)

    resp = client.get("/")
    assert resp.status_code == 200
    assert b"WHEEL" in resp.content
    assert b"EDITABLE" not in resp.content


def test_falls_back_to_editable_install_layout(monkeypatch, tmp_path):
    """When the wheel candidate is missing, the editable candidate is used."""
    missing_wheel = tmp_path / "wheel" / "_dashboard_dist"
    editable_dist = _make_dist(
        tmp_path / "editable" / "dashboard" / "frontend" / "dist",
        marker="EDITABLE",
    )
    _patch_candidates(monkeypatch, [missing_wheel, editable_dist])

    app = FastAPI()
    static_module.mount_frontend(app)
    client = TestClient(app)

    resp = client.get("/")
    assert resp.status_code == 200
    assert b"EDITABLE" in resp.content


def test_dist_without_assets_subdir_treated_as_missing(monkeypatch, tmp_path):
    """A bare index.html without ``assets/`` is half-built; fall through."""
    half_built = _make_dist(tmp_path / "wheel" / "_dashboard_dist", with_assets=False)
    good_dist = _make_dist(
        tmp_path / "editable" / "dashboard" / "frontend" / "dist",
        marker="GOOD",
    )
    _patch_candidates(monkeypatch, [half_built, good_dist])

    app = FastAPI()
    static_module.mount_frontend(app)
    client = TestClient(app)

    resp = client.get("/")
    assert resp.status_code == 200
    assert b"GOOD" in resp.content


# ---------- candidate-list structure smoke check ---------------------------


def test_candidate_dirs_contains_both_expected_layouts():
    """``_CANDIDATE_DIRS`` lists the wheel layout first, then editable."""
    candidates = static_module._CANDIDATE_DIRS
    assert len(candidates) == 2
    assert candidates[0].name == "_dashboard_dist"
    expected_editable_tail = Path("src") / "dashboard" / "frontend" / "dist"
    assert candidates[1].as_posix().endswith(expected_editable_tail.as_posix())
