"""``railway.json`` must keep pointing at a Dockerfile that exists.

Railway looks for a Dockerfile at the REPOSITORY ROOT. This project's is at
``src/voicegateway/Dockerfile``, so without this config Railway silently falls
back to Nixpacks and builds something other than the image we test and publish.
The failure is quiet: the deploy succeeds and runs a different artifact.

The path is a string in a JSON file, so nothing else in the build would notice
it going stale. Three top-level directories moved recently (``dashboards/`` ->
``deploy/grafana/``, ``benchmarks/`` and ``scripts/`` -> ``tools/``), which is
exactly the kind of change that would have broken it with no test failing.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3].parent
RAILWAY_JSON = REPO_ROOT / "railway.json"


def _config() -> dict:
    return json.loads(RAILWAY_JSON.read_text())


def test_the_config_exists_at_the_repository_root() -> None:
    """Railway reads it from the root; anywhere else needs manual UI config,
    which is the zero-config deploy this file exists to provide."""
    assert RAILWAY_JSON.is_file(), f"{RAILWAY_JSON} is missing"


def test_it_names_a_dockerfile_that_exists() -> None:
    """The guard that matters. A stale path here builds the wrong thing."""
    path = _config()["build"]["dockerfilePath"]
    assert (REPO_ROOT / path).is_file(), (
        f"railway.json points at {path}, which does not exist. Railway would "
        "fall back to Nixpacks and build an image nothing here tests."
    )


def test_it_selects_the_dockerfile_builder() -> None:
    assert _config()["build"]["builder"] == "DOCKERFILE"


def test_the_healthcheck_path_is_one_the_app_serves() -> None:
    """/health is unauthenticated by design, which is why it can be the probe.

    An authenticated path here would fail every healthcheck as soon as
    VOICEGW_API_KEY is set, which is the normal deployment.
    """
    probe = _config()["deploy"]["healthcheckPath"]
    assert probe == "/health"

    # CALLED, not introspected. This app includes its routers through a lazy
    # wrapper, so app.routes lists no paths; and a probe that resolves is the
    # actual requirement anyway. Unauthenticated on purpose: Railway sends no
    # credential, so an authenticated probe would fail every healthcheck the
    # moment VOICEGW_API_KEY is set, which is the normal deployment.
    from fastapi.testclient import TestClient

    from voicegateway.core.gateway import Gateway
    from voicegateway.server import build_app

    app = build_app(
        Gateway(require_config=False), enable_mcp_sse=False, enable_dashboard=False
    )
    with TestClient(app) as client:
        response = client.get(probe)
    assert response.status_code == 200, f"{probe} returned {response.status_code}"
    assert response.json()["status"] == "ok"
