"""Tests for branding endpoints (REQ-VG-ROUTE-004)."""

from __future__ import annotations

from io import BytesIO

import pytest
from fastapi.testclient import TestClient

import dashboard.api.main as api
from voicegateway.services.storage_service import StorageService


class _FakeGateway:
    def __init__(self, path: str):
        self.storage = StorageService(path)

        class _Cfg:
            class auth:
                api_keys = []
                cors_origins = []

            latency: dict = {}
            projects: dict = {}

        self.config = _Cfg()

    def list_projects(self):
        return []


@pytest.fixture
async def client(tmp_path, monkeypatch):
    path = str(tmp_path / "brand.db")
    gw = _FakeGateway(path)
    await gw.storage.upsert_managed_project("default", "Default")
    monkeypatch.setattr(api, "_gateway", gw)
    monkeypatch.setattr(api, "_cors_configured", True)
    yield gw, TestClient(api.app)


def test_get_branding_none_for_unbranded(client) -> None:
    _, c = client
    r = c.get("/api/projects/default/branding")
    assert r.status_code == 200
    assert r.json() == {"project_id": "default", "branding": None}


def test_get_branding_missing_project_returns_404(client) -> None:
    _, c = client
    r = c.get("/api/projects/missing/branding")
    assert r.status_code == 404


def test_post_branding_validates_and_persists(client) -> None:
    _, c = client
    r = c.post(
        "/api/projects/default/branding",
        json={"accent_color": "#FF6633", "product_name": "AcmeVoice"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["branding"]["accent_color"] == "#FF6633"
    assert data["branding"]["product_name"] == "AcmeVoice"

    # Re-GET surfaces persistence.
    r = c.get("/api/projects/default/branding")
    assert r.json()["branding"]["product_name"] == "AcmeVoice"


def test_post_branding_rejects_bad_hex(client) -> None:
    _, c = client
    r = c.post("/api/projects/default/branding", json={"accent_color": "red"})
    assert r.status_code == 400
    assert "hex" in r.json()["detail"].lower()


def test_post_branding_rejects_long_name(client) -> None:
    _, c = client
    r = c.post(
        "/api/projects/default/branding",
        json={"product_name": "x" * 100},
    )
    assert r.status_code == 400


def test_post_branding_rejects_unknown_key(client) -> None:
    _, c = client
    r = c.post("/api/projects/default/branding", json={"foo": "bar"})
    assert r.status_code == 400


def test_post_branding_missing_project_returns_404(client) -> None:
    _, c = client
    r = c.post("/api/projects/missing/branding", json={"accent_color": "#000"})
    assert r.status_code == 404


def test_logo_upload_oversized_returns_413(client) -> None:
    _, c = client
    big = b"x" * (260 * 1024)
    r = c.post(
        "/api/projects/default/branding/logo",
        files={"file": ("big.png", big, "image/png")},
    )
    assert r.status_code == 413
    assert "exceed" in r.json()["detail"].lower()


def test_logo_upload_svg_round_trip(client) -> None:
    _, c = client
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        b'<circle cx="5" cy="5" r="4"/></svg>'
    )
    r = c.post(
        "/api/projects/default/branding/logo",
        files={"file": ("logo.svg", svg, "image/svg+xml")},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["format"] == "SVG"
    assert data["logo_url"].endswith("/default.svg")


def test_logo_upload_rejects_unknown_format(client) -> None:
    _, c = client
    r = c.post(
        "/api/projects/default/branding/logo",
        files={"file": ("logo.jpg", b"bytes", "image/jpeg")},
    )
    assert r.status_code == 400


def test_logo_upload_png_validates_dimensions(client) -> None:
    from PIL import Image

    _, c = client
    # 600x600 PNG should be rejected (cap is 512x512).
    img = Image.new("RGB", (600, 600), color="blue")
    buf = BytesIO()
    img.save(buf, format="PNG")
    r = c.post(
        "/api/projects/default/branding/logo",
        files={"file": ("big.png", buf.getvalue(), "image/png")},
    )
    assert r.status_code == 400
    assert "512" in r.json()["detail"]


def test_logo_upload_png_accepts_valid(client) -> None:
    from PIL import Image

    _, c = client
    img = Image.new("RGB", (128, 128), color="red")
    buf = BytesIO()
    img.save(buf, format="PNG")
    r = c.post(
        "/api/projects/default/branding/logo",
        files={"file": ("logo.png", buf.getvalue(), "image/png")},
    )
    assert r.status_code == 200
    assert r.json()["format"] == "PNG"
