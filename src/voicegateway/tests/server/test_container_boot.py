"""The published image must boot on a host that mounts nothing.

The Dockerfile bakes ``VOICEGW_CONFIG=/data/voicegw.yaml`` and nothing in the
image creates that file. ``main()`` passed it to ``Gateway``, which loaded it
unconditionally, and a missing file raised ``ConfigError`` BEFORE
``uvicorn.run`` bound a port. Every host that mounts no volume there crash
-looped: Railway and Fly both did, and the platform reported a failing
healthcheck or no healthy upstream, so the actual cause (one missing file)
appeared nowhere in the message a user saw.

An empty config is entirely valid. A collector started from the image with
``VOICEGW_DB_URL`` and ``VOICEGW_API_KEY`` needs nothing from a YAML file, so
requiring one bought nothing and cost the deploy.

The strict default is kept for everyone else, and pinned here: a mistyped
``--config`` must stay a hard error, not a silent start with none of the
providers its operator wrote down.
"""

from __future__ import annotations

import os

import pytest

from voicegateway.core.config import ConfigError, GatewayConfig
from voicegateway.core.gateway import Gateway


@pytest.fixture(autouse=True)
def _no_ambient_config(monkeypatch, tmp_path):
    """Run from a directory with no ./voicegw.yaml, so the search list is empty.

    Without this the repo's own voicegw.yaml (or the developer's ~/.config one)
    satisfies the search and the tests below prove nothing.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VOICEGW_CONFIG", raising=False)
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    monkeypatch.delenv("VOICEGW_DB_URL", raising=False)
    monkeypatch.setattr(
        "voicegateway.core.config._CONFIG_PATHS", [tmp_path / "voicegw.yaml"]
    )


# --------------------------------------------------------------------------
# The container case
# --------------------------------------------------------------------------


def test_a_baked_config_path_that_does_not_exist_still_boots() -> None:
    """The exact shape the image ships: a path set, and no file at it."""
    gw = Gateway(config_path="/data/voicegw.yaml", require_config=False)
    assert gw.config.providers == {}


def test_no_config_anywhere_still_boots() -> None:
    """The other container shape: nothing set and nothing on disk."""
    assert Gateway(require_config=False).config.providers == {}


def test_the_loader_returns_defaults_rather_than_raising() -> None:
    cfg = GatewayConfig.load("/data/voicegw.yaml", required=False)
    assert cfg.providers == {}
    assert cfg.auth.api_keys == []


def test_storage_still_comes_from_the_environment_without_a_file(
    monkeypatch, tmp_path
) -> None:
    """The reason an empty config is enough for a collector.

    Requiring a YAML file bought nothing here: everything this deployment needs
    is already in the environment.
    """
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "collector.db"))
    gw = Gateway(config_path="/data/voicegw.yaml", require_config=False)
    assert gw.storage is not None


# --------------------------------------------------------------------------
# The strict default is unchanged
# --------------------------------------------------------------------------


def test_an_explicit_missing_config_is_still_an_error() -> None:
    """A mistyped --config must not silently start with no providers."""
    with pytest.raises(ConfigError, match="not found"):
        GatewayConfig.load("/nope/voicegw.yaml")


def test_no_config_anywhere_is_still_an_error_by_default() -> None:
    with pytest.raises(ConfigError, match="voicegw init"):
        GatewayConfig.load()


def test_gateway_defaults_to_strict() -> None:
    """The opt-in is the server entrypoint's, not everybody's."""
    with pytest.raises(ConfigError):
        Gateway(config_path="/nope/voicegw.yaml")


def test_a_config_that_exists_is_still_read_when_not_required(tmp_path) -> None:
    """Non-vacuous: require_config=False does not mean "ignore the file"."""
    cfg_file = tmp_path / "voicegw.yaml"
    cfg_file.write_text("providers:\n  openai:\n    api_key: sk-real\n")
    cfg = GatewayConfig.load(str(cfg_file), required=False)
    assert "openai" in cfg.providers


# --------------------------------------------------------------------------
# The port the platform asks for
# --------------------------------------------------------------------------


def _resolved_port() -> int:
    """The expression main() uses, kept in one place so the test cannot drift."""
    return int(os.environ.get("VOICEGW_PORT") or os.environ.get("PORT") or "8080")


def test_port_is_honoured_so_no_platform_needs_manual_wiring(monkeypatch) -> None:
    """Railway, Render, Heroku and Cloud Run all inject PORT."""
    monkeypatch.delenv("VOICEGW_PORT", raising=False)
    monkeypatch.setenv("PORT", "7788")
    assert _resolved_port() == 7788


def test_voicegw_port_wins_over_port(monkeypatch) -> None:
    """An operator who set the explicit one keeps their value."""
    monkeypatch.setenv("VOICEGW_PORT", "9999")
    monkeypatch.setenv("PORT", "7788")
    assert _resolved_port() == 9999


def test_the_default_is_still_the_port_the_dockerfile_exposes(monkeypatch) -> None:
    """8080 is EXPOSEd and healthchecked in the image; it must not move."""
    monkeypatch.delenv("VOICEGW_PORT", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    assert _resolved_port() == 8080


def test_main_reads_the_port_the_same_way() -> None:
    """Pins the helper above against the real entrypoint, so they cannot drift."""
    import inspect

    from voicegateway.server.main import main

    src = inspect.getsource(main)
    assert 'os.environ.get("VOICEGW_PORT") or os.environ.get("PORT")' in src
    assert "require_config=False" in src
