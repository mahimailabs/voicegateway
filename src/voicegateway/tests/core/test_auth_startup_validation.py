"""Local development means local. The server refuses contradictory config.

The roadmap requires unauthenticated mode to be explicit and unavailable in
production configuration. A flag alone is not enough: an operator who sets it
on a laptop and later copies the file to a server would carry an open
deployment with it. So the flag is refused at startup in the company of
anything production-shaped, and the message names which line to fix.
"""

from __future__ import annotations

import pytest

from voicegateway.core.auth import AuthConfigError, validate_auth_startup
from voicegateway.schemas.config_schema import ApiKeyEntry, AuthConfig


def test_defaults_are_warn_mode_and_not_local():
    """0.26.0 ships warn: existing deployments keep working and get told."""
    cfg = AuthConfig()
    assert cfg.local_development is False
    assert cfg.enforcement == "warn"


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_local_development_on_loopback_is_fine(host):
    validate_auth_startup(AuthConfig(local_development=True), bind_host=host)


def test_local_development_refuses_a_public_bind():
    with pytest.raises(AuthConfigError, match="bind host"):
        validate_auth_startup(AuthConfig(local_development=True), bind_host="0.0.0.0")


def test_local_development_refuses_configured_keys():
    """Minting keys and then disabling auth is a contradiction, not a config."""
    cfg = AuthConfig(
        local_development=True,
        api_keys=[ApiKeyEntry(token="t", name="n", scopes=["read"])],
    )
    with pytest.raises(AuthConfigError, match="api_keys"):
        validate_auth_startup(cfg, bind_host="127.0.0.1")


def test_local_development_refuses_enforce_mode():
    with pytest.raises(AuthConfigError, match="enforcement"):
        validate_auth_startup(
            AuthConfig(local_development=True, enforcement="enforce"),
            bind_host="127.0.0.1",
        )


def test_every_conflict_is_named_at_once():
    """One restart per problem is a bad afternoon. List them all."""
    cfg = AuthConfig(
        local_development=True,
        enforcement="enforce",
        api_keys=[ApiKeyEntry(token="t", name="n", scopes=["read"])],
    )
    with pytest.raises(AuthConfigError) as exc:
        validate_auth_startup(cfg, bind_host="0.0.0.0")
    message = str(exc.value)
    assert "api_keys" in message
    assert "bind host" in message
    assert "enforcement" in message


def test_production_shaped_config_passes():
    """Not local: a public bind under enforce is exactly what production is."""
    validate_auth_startup(AuthConfig(enforcement="enforce"), bind_host="0.0.0.0")


def test_validation_is_a_no_op_when_not_local():
    """Nothing else is second-guessed. This guard has one job."""
    cfg = AuthConfig(api_keys=[ApiKeyEntry(token="t", name="n", scopes=["read"])])
    validate_auth_startup(cfg, bind_host="0.0.0.0")


def test_enforcement_rejects_an_unknown_mode():
    """A typo must fail loudly at load, not silently mean warn."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AuthConfig(enforcement="enforced")


def test_serve_refuses_to_start_on_a_contradictory_config(tmp_path, monkeypatch):
    """The validator is wired into serve, not merely importable.

    A guard nothing calls is the same as no guard, and this one only fires on
    a path that binds a socket, so it is easy to leave unwired without any
    test noticing.
    """
    import re

    import yaml
    from typer.testing import CliRunner

    from voicegateway.cli._app import app

    config = tmp_path / "voicegw.yaml"
    config.write_text(
        yaml.dump(
            {
                "providers": {"openai": {"api_key": "k"}},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "projects": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "auth": {
                    "local_development": True,
                    "api_keys": [{"token": "t", "name": "n", "scopes": ["read"]}],
                },
            }
        )
    )
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "serve.db"))
    # serve_cmd does os.environ.setdefault("VOICEGW_CONFIG", config), a
    # process-global that outlives this test and would point later CLI tests
    # at a deleted tmp directory. Claim it through monkeypatch first so the
    # setdefault is a no-op and the original value is restored on teardown.
    monkeypatch.setenv("VOICEGW_CONFIG", str(config))

    result = CliRunner().invoke(
        app, ["serve", "--config", str(config), "--host", "0.0.0.0", "--port", "8099"]
    )

    assert result.exit_code != 0
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "local_development" in plain
    assert "api_keys" in plain
    assert "not loopback" in plain


# --------------------------------------------------------------------------
# Defense in depth: serve is not the only door
# --------------------------------------------------------------------------


def test_bind_host_is_optional_so_hostless_callers_can_still_validate():
    """build_app has no bind host, but can still catch the config conflicts."""
    from voicegateway.schemas.config_schema import ApiKeyEntry as Entry

    # Loopback check is skipped, the other two still apply.
    validate_auth_startup(AuthConfig(local_development=True), bind_host=None)
    with pytest.raises(AuthConfigError, match="api_keys"):
        validate_auth_startup(
            AuthConfig(
                local_development=True,
                api_keys=[Entry(token="t", name="n", scopes=["read"])],
            ),
            bind_host=None,
        )
    with pytest.raises(AuthConfigError, match="enforcement"):
        validate_auth_startup(
            AuthConfig(local_development=True, enforcement="enforce"), bind_host=None
        )


def test_build_app_refuses_a_contradictory_config(tmp_path, monkeypatch):
    """serve is not the only door.

    ``python -m voicegateway.server.main`` is what the container runs, and it
    binds 0.0.0.0 by default. A guard that only lives in the serve CLI would
    refuse an unauthenticated public deployment on a laptop and wave it
    through in production, which is the wrong way round.
    """
    import yaml

    from voicegateway.core.gateway import Gateway
    from voicegateway.server import build_app

    config = tmp_path / "voicegw.yaml"
    config.write_text(
        yaml.dump(
            {
                "providers": {"openai": {"api_key": "k"}},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "projects": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "auth": {
                    "local_development": True,
                    "api_keys": [{"token": "t", "name": "n", "scopes": ["read"]}],
                },
            }
        )
    )
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "build.db"))

    gateway = Gateway(config_path=str(config))
    with pytest.raises(AuthConfigError, match="api_keys"):
        build_app(gateway, enable_mcp_sse=False, enable_dashboard=False)


def test_build_app_accepts_a_coherent_local_config(tmp_path, monkeypatch):
    """A genuinely local config must still build, or dev is broken."""
    import yaml

    from voicegateway.core.gateway import Gateway
    from voicegateway.server import build_app

    config = tmp_path / "voicegw.yaml"
    config.write_text(
        yaml.dump(
            {
                "providers": {"openai": {"api_key": "k"}},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "projects": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "auth": {"local_development": True},
            }
        )
    )
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "build_ok.db"))

    gateway = Gateway(config_path=str(config))
    assert build_app(gateway, enable_mcp_sse=False, enable_dashboard=False) is not None
