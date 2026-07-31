"""Tests for branding endpoints (REQ-VG-ROUTE-004).

After the dashboard fold-in, these handlers live in
:mod:`voicegateway.server.api.dashboard.branding` and are reached
through the daemon's ``build_app(...)``.

The sections after the original v0.5.0 tests cover the three defects the
security audit found in the logo upload: it had no auth dependency at all, it
accepted any bytes containing ``<svg`` and served them same-origin, and it
interpolated the project id straight into a path inside the directory that
holds the repo's own shipped brand assets.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from voicegateway.core.auth import ApiKey
from voicegateway.core.gateway import Gateway
from voicegateway.server.api.dashboard import branding as branding_mod
from voicegateway.server.main import build_app

# Stand-in for one of the assets that ship in the repo's branding directory
# (default.svg, logo.svg, favicon.svg, wordings.png). Distinctive so a clobber
# is visible in the assertion message rather than inferred.
_SHIPPED_ASSET = b'<svg xmlns="http://www.w3.org/2000/svg"><!-- shipped --></svg>'

_CLEAN_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
    b'<circle cx="5" cy="5" r="4"/></svg>'
)


@pytest.fixture(autouse=True)
def sandbox_branding_dir(tmp_path, monkeypatch):
    """Point the branding directory at tmp_path for every test in this module.

    Autouse so it is in place before ``build_app`` mounts StaticFiles on the
    directory. Without it the upload tests write into
    ``src/dashboard/api/static/branding/``, which is TRACKED: that is how the
    repo's own ``default.svg`` came to hold this file's SVG fixture. The
    sandbox is seeded with a stand-in shipped asset so the no-clobber
    assertions below have something real to check.
    """
    sandbox = tmp_path / "branding"
    sandbox.mkdir()
    (sandbox / "default.svg").write_bytes(_SHIPPED_ASSET)
    (sandbox / "logo.svg").write_bytes(_SHIPPED_ASSET)
    monkeypatch.setattr(branding_mod, "_BRANDING_DIR", sandbox)
    return sandbox


@pytest.fixture
async def client(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "brand.db"))
    gw = Gateway(config_path=temp_config)
    await gw.storage.upsert_managed_project("default", "Default")
    app = build_app(gw, enable_mcp_sse=False, enable_dashboard=True)
    yield gw, TestClient(app)


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


def _upload_svg(c, svg: bytes, project: str = "default"):
    return c.post(
        f"/api/projects/{project}/branding/logo",
        files={"file": ("logo.svg", svg, "image/svg+xml")},
    )


# ---------------------------------------------------------------------------
# Auth: the upload is an admin write, and carried no dependency at all
# ---------------------------------------------------------------------------


def test_logo_upload_open_when_no_keys_are_configured(client) -> None:
    """The self-hosted default (no keys configured) is unchanged.

    ``core.auth.check_request`` returns None on an empty key list, so the
    admin gate costs the single-operator deployment nothing.
    """
    _, c = client
    assert c.app.state.api_keys == []
    assert _upload_svg(c, _CLEAN_SVG).status_code == 200


def test_logo_upload_requires_admin_when_auth_is_enabled(client, sandbox_branding_dir):
    """With keys configured, only an admin-scoped caller may write bytes.

    Before this, the route had no dependency: any caller who could reach the
    port could put a file into a directory the dashboard serves from its own
    origin, on a deployment that otherwise required a token for every read.
    """
    _, c = client
    c.app.state.api_keys = [
        ApiKey(token="read-token", name="viewer", scopes=("read",)),
        ApiKey(token="admin-token", name="operator", scopes=("admin",)),
    ]

    anon = _upload_svg(c, _CLEAN_SVG)
    assert anon.status_code == 401, anon.text

    wrong = c.post(
        "/api/projects/default/branding/logo",
        files={"file": ("logo.svg", _CLEAN_SVG, "image/svg+xml")},
        headers={"Authorization": "Bearer nope"},
    )
    assert wrong.status_code == 401, wrong.text

    reader = c.post(
        "/api/projects/default/branding/logo",
        files={"file": ("logo.svg", _CLEAN_SVG, "image/svg+xml")},
        headers={"Authorization": "Bearer read-token"},
    )
    assert reader.status_code == 403, reader.text

    # Nothing landed on disk for any of the three refusals.
    assert not (sandbox_branding_dir / "uploads").exists()

    admin = c.post(
        "/api/projects/default/branding/logo",
        files={"file": ("logo.svg", _CLEAN_SVG, "image/svg+xml")},
        headers={"Authorization": "Bearer admin-token"},
    )
    assert admin.status_code == 200, admin.text
    assert (sandbox_branding_dir / "uploads" / "default.svg").read_bytes() == _CLEAN_SVG


def test_post_branding_open_when_no_keys_are_configured(client) -> None:
    """The self-hosted default (no keys configured) is unchanged.

    ``core.auth.check_request`` returns None on an empty key list, so the
    admin gate costs the single-operator deployment nothing here either.
    """
    _, c = client
    assert c.app.state.api_keys == []
    r = c.post("/api/projects/default/branding", json={"product_name": "LocalVoice"})
    assert r.status_code == 200, r.text
    assert r.json()["branding"]["product_name"] == "LocalVoice"


def test_post_branding_requires_admin_when_auth_is_enabled(client) -> None:
    """The branding write takes the same gate its upload sibling took.

    Gating only the upload left the interesting half open: ``logo_url`` is an
    arbitrary string here (the upload is one way to produce one, not the only
    way), and ``product_name`` is what every dashboard page then renders as
    its own name. An anonymous caller gets 401, a read-scoped token 403, and
    the stored branding is untouched by both.
    """
    _, c = client
    c.app.state.api_keys = [
        ApiKey(token="read-token", name="viewer", scopes=("read",)),
        ApiKey(token="admin-token", name="operator", scopes=("admin",)),
    ]
    payload = {"product_name": "Impostor", "accent_color": "#FF0000"}

    anon = c.post("/api/projects/default/branding", json=payload)
    assert anon.status_code == 401, anon.text

    wrong = c.post(
        "/api/projects/default/branding",
        json=payload,
        headers={"Authorization": "Bearer nope"},
    )
    assert wrong.status_code == 401, wrong.text

    reader = c.post(
        "/api/projects/default/branding",
        json=payload,
        headers={"Authorization": "Bearer read-token"},
    )
    assert reader.status_code == 403, reader.text

    # Nothing landed for any of the three refusals.
    unchanged = c.get(
        "/api/projects/default/branding",
        headers={"Authorization": "Bearer read-token"},
    )
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["branding"] is None

    admin = c.post(
        "/api/projects/default/branding",
        json=payload,
        headers={"Authorization": "Bearer admin-token"},
    )
    assert admin.status_code == 200, admin.text
    assert admin.json()["branding"]["product_name"] == "Impostor"


def test_branding_read_is_not_gated_behind_the_admin_scope(client) -> None:
    """The GET stays a read: the admin gate is on the upload route only.

    Every dashboard layout mount calls this endpoint to apply the brand
    (``lib/branding.ts``), and the FE treats 403 the same as 401: it clears
    the stored token and shows the login gate. Putting the admin dependency
    on the whole router would log out a read-scoped operator on page load.
    """
    _, c = client
    c.app.state.api_keys = [ApiKey(token="read-token", name="viewer", scopes=("read",))]
    r = c.get(
        "/api/projects/default/branding",
        headers={"Authorization": "Bearer read-token"},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Stored XSS: an uploaded SVG is an active document, not an image
# ---------------------------------------------------------------------------

# Each payload is a file the pre-hardening check accepted: it starts with
# "<svg" inside the first 512 bytes, which was the entire validation.
_MALICIOUS_SVGS = {
    "script_element": (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<script>fetch("/api/api_keys")</script></svg>'
    ),
    "event_handler": (
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(document.domain)">'
        b'<circle cx="5" cy="5" r="4"/></svg>'
    ),
    "event_handler_on_child": (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<circle cx="5" cy="5" r="4" onclick="alert(1)"/></svg>'
    ),
    "foreign_object": (
        b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject>'
        b'<body xmlns="http://www.w3.org/1999/xhtml">hi</body></foreignObject></svg>'
    ),
    "javascript_url": (
        b'<svg xmlns="http://www.w3.org/2000/svg" '
        b'xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<a xlink:href="javascript:alert(1)"><text>x</text></a></svg>'
    ),
    "data_url": (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<image href="data:text/html;base64,PHNjcmlwdD4="/></svg>'
    ),
    "entity_declaration": (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<!ENTITY xxe SYSTEM "file:///etc/passwd">&xxe;</svg>'
    ),
    "animate_retargeting_href": (
        b'<svg xmlns="http://www.w3.org/2000/svg"><a><animate attributeName="href" '
        b'values="javascript:alert(1)"/><text>x</text></a></svg>'
    ),
}


@pytest.mark.parametrize("name", sorted(_MALICIOUS_SVGS))
def test_logo_upload_rejects_executable_svg(client, sandbox_branding_dir, name) -> None:
    """Every executable SVG shape is refused, and nothing reaches disk.

    These are served same-origin from ``/static/branding``; an SVG opened as a
    top-level document runs its script in the dashboard's origin, where the
    dashboard's bearer token lives in localStorage.
    """
    _, c = client
    resp = _upload_svg(c, _MALICIOUS_SVGS[name])
    assert resp.status_code == 400, f"{name} was accepted: {resp.text}"
    assert not (sandbox_branding_dir / "uploads").exists(), (
        f"{name} was refused but still written to disk"
    )


def test_logo_upload_rejects_malformed_xml(client) -> None:
    """Markup a strict parser rejects but a lenient browser parser may run."""
    _, c = client
    resp = _upload_svg(c, b'<svg xmlns="http://www.w3.org/2000/svg"><circle>')
    assert resp.status_code == 400
    assert "well-formed" in resp.json()["detail"]


def test_logo_upload_still_accepts_an_ordinary_vector_logo(
    client, sandbox_branding_dir
) -> None:
    """The rejection rules do not cost a real logo: paths, groups, styles."""
    _, c = client
    svg = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" '
        b'width="64" height="64"><title>Acme</title>'
        b'<g fill="none" stroke="#1F96AA" stroke-width="4">'
        b'<path d="M8 56 L32 8 L56 56 Z" style="stroke-linejoin:round"/>'
        b'</g><rect x="0" y="0" width="64" height="64" fill="#fff" '
        b'opacity="0.1"/></svg>'
    )
    resp = _upload_svg(c, svg)
    assert resp.status_code == 200, resp.text
    assert (sandbox_branding_dir / "uploads" / "default.svg").read_bytes() == svg


def test_svg_scan_sees_through_utf16_encoding() -> None:
    """The raw pass normalises UTF-16 ASCII onto the same markers.

    A byte scan for ``<script`` misses ``<\\x00s\\x00c...``; stripping the
    control range before matching does not. Asserted on the checker directly
    because the legacy ``b"<svg" in content[:512]`` sniff refuses a UTF-16
    file earlier in the handler, which would hide whether this pass works.
    """
    from fastapi import HTTPException

    payload = '<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>'
    with pytest.raises(HTTPException) as excinfo:
        branding_mod._reject_unsafe_svg(payload.encode("utf-16-le"))
    assert excinfo.value.status_code == 400
    assert "<script" in excinfo.value.detail


# ---------------------------------------------------------------------------
# Path confinement: the audit's upload replaced a TRACKED repo asset
# ---------------------------------------------------------------------------


def test_upload_never_overwrites_a_shipped_branding_asset(
    client, sandbox_branding_dir
) -> None:
    """Regression: uploading for project "default" replaced default.svg.

    The branding root holds assets that ship in the repo (default.svg,
    logo.svg, favicon.svg, wordings.png) and the handler wrote
    ``<root>/{project_id}.{ext}``, so a project whose id matched an asset stem
    silently replaced a tracked file with attacker-supplied bytes. Uploads now
    land one level down, where they cannot name a shipped asset at all.
    """
    _, c = client
    resp = _upload_svg(c, _CLEAN_SVG)
    assert resp.status_code == 200, resp.text

    assert (sandbox_branding_dir / "default.svg").read_bytes() == _SHIPPED_ASSET
    assert (sandbox_branding_dir / "logo.svg").read_bytes() == _SHIPPED_ASSET
    assert (sandbox_branding_dir / "uploads" / "default.svg").read_bytes() == _CLEAN_SVG
    assert resp.json()["logo_url"] == "/static/branding/uploads/default.svg"


@pytest.mark.parametrize(
    "project_id",
    [
        "../../default",
        "../../../../etc/passwd",
        "..\\..\\default",
        "..",
        ".",
        "a/b",
        "/absolute",
        "with\x00nul",
        "with space",
        "unicode-é",
        "",
    ],
)
def test_hostile_project_id_cannot_escape_the_upload_directory(
    sandbox_branding_dir, project_id
) -> None:
    """The write path is confined for any project id, however hostile.

    Asserted on the resolver rather than over HTTP because Starlette's path
    converter is ``[^/]+``: an encoded separator never routes (pinned by
    ``test_encoded_traversal_does_not_route`` below). That is the second line
    of defence, not the first. A project id is an arbitrary string with no
    validation at creation, and a proxy that decodes ``%2F`` before forwarding
    would change what routes without changing this guarantee.
    """
    root = (sandbox_branding_dir / "uploads").resolve()
    target = branding_mod._resolve_logo_target(project_id, "svg")

    assert target.parent == root, f"{project_id!r} escaped to {target}"
    assert target.name.endswith(".svg")
    assert "/" not in target.name and "\\" not in target.name
    assert "\x00" not in target.name
    # And it can never name one of the shipped assets, which live one level up.
    assert target != sandbox_branding_dir / "default.svg"


def test_safe_project_id_keeps_a_readable_filename(sandbox_branding_dir) -> None:
    """The ordinary id is still used verbatim, so the URL stays legible."""
    assert branding_mod._logo_filename("acme", "png") == "acme.png"
    assert branding_mod._logo_filename("acme_2-eu", "svg") == "acme_2-eu.svg"
    # Anything outside the allow-list collapses to a digest, deterministically.
    hostile = branding_mod._logo_filename("../../default", "svg")
    assert hostile.startswith("p-") and hostile.endswith(".svg")
    assert branding_mod._logo_filename("../../default", "svg") == hostile


def test_encoded_traversal_does_not_route(client, sandbox_branding_dir) -> None:
    """An encoded separator in the id never reaches the handler.

    Starlette's path converter is ``[^/]+``, so the upload route does not
    match. 405 rather than 404 because the dashboard's SPA fallback claims the
    unmatched path for GET; either way no handler runs and nothing is written.
    """
    _, c = client
    resp = c.post(
        "/api/projects/..%2F..%2Fdefault/branding/logo",
        files={"file": ("logo.svg", _CLEAN_SVG, "image/svg+xml")},
    )
    assert resp.status_code in (404, 405), resp.text
    assert (sandbox_branding_dir / "default.svg").read_bytes() == _SHIPPED_ASSET
    assert not (sandbox_branding_dir / "uploads").exists()


# ---------------------------------------------------------------------------
# The served response is inert even if a payload ever gets past the checks
# ---------------------------------------------------------------------------


def test_branding_assets_are_served_with_a_sandbox_csp(client) -> None:
    """The static mount carries the defence that does not depend on the check.

    Content inspection is a denylist and denylists lose eventually; the CSP
    is what makes the loss survivable. ``sandbox`` alone puts the response in
    an opaque origin, so script in a branding asset cannot read the
    dashboard's localStorage even if it runs.
    """
    _, c = client
    assert _upload_svg(c, _CLEAN_SVG).status_code == 200

    for path in ("/static/branding/uploads/default.svg", "/static/branding/logo.svg"):
        resp = c.get(path)
        assert resp.status_code == 200, f"{path}: {resp.status_code}"
        csp = resp.headers.get("content-security-policy")
        assert csp is not None, f"{path} served with no CSP"
        assert "sandbox" in csp
        assert "default-src 'none'" in csp
        assert resp.headers.get("x-content-type-options") == "nosniff"
