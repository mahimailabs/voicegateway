"""Tests for the HTTP API's bearer-token auth layer.

Covers the ``voicegateway/core/auth.py`` primitives directly (mirrors the
style of ``tests/mcp/test_auth.py``) and the end-to-end enforcement on
``voicegateway/server.py`` via FastAPI's TestClient.
"""

from __future__ import annotations

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.auth import (
    ApiKey,
    AuthError,
    check_request,
    load_api_keys,
    resolve_cors_origins,
)
from voicegateway.core.config import AuthConfig
from voicegateway.core.gateway import Gateway
from voicegateway.server import build_app

# ---------------------------------------------------------------------------
# Unit: core/auth.py primitives
# ---------------------------------------------------------------------------


def test_no_keys_configured_passes_any_header(monkeypatch):
    """No YAML keys, no env var → check_request returns None for anything."""
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    keys = load_api_keys(AuthConfig())
    assert keys == []
    assert check_request(None, "write", keys) is None
    assert check_request("Bearer anything", "write", keys) is None


def test_env_shortcut_synthesizes_wildcard_key(monkeypatch):
    monkeypatch.setenv("VOICEGW_API_KEY", "env-token")
    keys = load_api_keys(AuthConfig())
    assert len(keys) == 1
    assert keys[0].token == "env-token"
    assert keys[0].scopes == ("*",)
    assert keys[0].name == "env"


def test_yaml_keys_skip_empty_tokens(monkeypatch):
    """Unresolved ${ENV_VAR} substitutions produce empty tokens; skip them."""
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    cfg = AuthConfig(
        api_keys=[
            {"token": "", "name": "placeholder"},
            {"token": "real", "name": "live"},
        ]
    )
    keys = load_api_keys(cfg)
    assert len(keys) == 1
    assert keys[0].token == "real"


def test_missing_header_rejected():
    keys = [ApiKey(token="s", name="x", scopes=("*",))]
    with pytest.raises(AuthError) as exc:
        check_request(None, "write", keys)
    assert exc.value.status_code == 401
    assert "bearer" in exc.value.message.lower()


def test_wrong_scheme_rejected():
    keys = [ApiKey(token="s", name="x", scopes=("*",))]
    with pytest.raises(AuthError) as exc:
        check_request("Basic dXNlcjpwYXNz", "write", keys)
    assert exc.value.status_code == 401


def test_wrong_token_rejected():
    keys = [ApiKey(token="s", name="x", scopes=("*",))]
    with pytest.raises(AuthError) as exc:
        check_request("Bearer nope", "write", keys)
    assert exc.value.status_code == 401
    assert "invalid" in exc.value.message.lower()


def test_correct_token_matches():
    keys = [ApiKey(token="s", name="x", scopes=("*",))]
    matched = check_request("Bearer s", "write", keys)
    assert matched is keys[0]


def test_scope_enforced_403():
    """Valid token, insufficient scope → 403 distinct from 401."""
    keys = [ApiKey(token="readonly", name="ro", scopes=("read",))]
    with pytest.raises(AuthError) as exc:
        check_request("Bearer readonly", "write", keys)
    assert exc.value.status_code == 403
    assert "scope" in exc.value.message.lower()


def test_scope_read_token_passes_read():
    keys = [ApiKey(token="readonly", name="ro", scopes=("read",))]
    matched = check_request("Bearer readonly", "read", keys)
    assert matched is keys[0]


def test_multiple_keys_any_matches():
    keys = [
        ApiKey(token="alpha", name="a", scopes=("*",)),
        ApiKey(token="beta", name="b", scopes=("read",)),
    ]
    assert check_request("Bearer alpha", "write", keys) is keys[0]
    # beta has only 'read' scope — write fails
    with pytest.raises(AuthError) as exc:
        check_request("Bearer beta", "write", keys)
    assert exc.value.status_code == 403


def test_uses_constant_time_comparison():
    """Smoke check that hmac.compare_digest is on the code path."""
    import inspect

    from voicegateway.core import auth

    source = inspect.getsource(auth)
    assert "hmac.compare_digest" in source


def test_resolve_cors_origins_default_is_wildcard():
    assert resolve_cors_origins(None) == ["*"]
    assert resolve_cors_origins(AuthConfig()) == ["*"]


def test_resolve_cors_origins_from_config():
    cfg = AuthConfig(cors_origins=["http://a", "http://b"])
    assert resolve_cors_origins(cfg) == ["http://a", "http://b"]


# ---------------------------------------------------------------------------
# End-to-end: FastAPI dependency on mutating endpoints
# ---------------------------------------------------------------------------

_BASE_CONFIG = {
    "providers": {
        "openai": {"api_key": "test-key"},
        "deepgram": {"api_key": "test-key"},
    },
    "models": {
        "stt": {"deepgram/nova-3": {"provider": "deepgram", "model": "nova-3"}},
        "llm": {"openai/gpt-4o-mini": {"provider": "openai", "model": "gpt-4o-mini"}},
        "tts": {},
    },
    "projects": {},
    "fallbacks": {"stt": [], "llm": [], "tts": []},
    "cost_tracking": {"enabled": True},
    "observability": {
        "latency_tracking": True,
        "cost_tracking": True,
        "request_logging": True,
    },
}


def _write_config(tmp_path, auth_block):
    cfg = {**_BASE_CONFIG}
    if auth_block is not None:
        cfg = {**cfg, "auth": auth_block}
    path = tmp_path / "voicegw.yaml"
    with open(path, "w") as f:
        yaml.dump(cfg, f)
    return str(path)


@pytest.fixture
def gateway_factory(tmp_path, monkeypatch):
    """Build a Gateway with a caller-supplied auth block."""

    def _build(auth_block):
        monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "auth.db"))
        # Ensure no env shortcut leaks into tests that expect no auth.
        monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
        path = _write_config(tmp_path, auth_block)
        return Gateway(config_path=path)

    return _build


async def _client(gateway):
    app = build_app(gateway)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), app


async def test_no_auth_config_mutations_open(gateway_factory):
    """No auth block, no env key → POST works without Authorization header."""
    gw = gateway_factory(None)
    client, _ = await _client(gw)
    async with client as c:
        resp = await c.post(
            "/v1/providers",
            json={"provider_id": "ollama-x", "provider_type": "ollama", "api_key": ""},
        )
        assert resp.status_code == 200


async def test_write_endpoint_requires_token(gateway_factory):
    gw = gateway_factory(
        {"api_keys": [{"token": "secret", "name": "t", "scopes": ["*"]}]}
    )
    client, _ = await _client(gw)
    async with client as c:
        resp = await c.post(
            "/v1/providers",
            json={"provider_id": "ollama-x", "provider_type": "ollama", "api_key": ""},
        )
        assert resp.status_code == 401


async def test_write_endpoint_wrong_token(gateway_factory):
    gw = gateway_factory(
        {"api_keys": [{"token": "secret", "name": "t", "scopes": ["*"]}]}
    )
    client, _ = await _client(gw)
    async with client as c:
        resp = await c.post(
            "/v1/providers",
            headers={"Authorization": "Bearer wrong"},
            json={"provider_id": "ollama-x", "provider_type": "ollama", "api_key": ""},
        )
        assert resp.status_code == 401


async def test_write_endpoint_correct_token(gateway_factory):
    gw = gateway_factory(
        {"api_keys": [{"token": "secret", "name": "t", "scopes": ["*"]}]}
    )
    client, _ = await _client(gw)
    async with client as c:
        resp = await c.post(
            "/v1/providers",
            headers={"Authorization": "Bearer secret"},
            json={"provider_id": "ollama-x", "provider_type": "ollama", "api_key": ""},
        )
        assert resp.status_code == 200


async def test_read_scope_rejected_on_write(gateway_factory):
    gw = gateway_factory(
        {"api_keys": [{"token": "ro", "name": "readonly", "scopes": ["read"]}]}
    )
    client, _ = await _client(gw)
    async with client as c:
        resp = await c.post(
            "/v1/providers",
            headers={"Authorization": "Bearer ro"},
            json={"provider_id": "ollama-x", "provider_type": "ollama", "api_key": ""},
        )
        assert resp.status_code == 403


async def test_reads_open_regardless_of_auth(gateway_factory):
    """This slice leaves GET endpoints open; confirm the ones the audit
    flagged as sensitive (/v1/costs, /v1/logs, /v1/projects) still
    respond 200 without a token."""
    gw = gateway_factory(
        {"api_keys": [{"token": "secret", "name": "t", "scopes": ["*"]}]}
    )
    client, _ = await _client(gw)
    async with client as c:
        for path in ("/v1/costs", "/v1/logs", "/v1/projects"):
            resp = await c.get(path)
            assert resp.status_code == 200, path


async def test_health_and_metrics_always_public(gateway_factory):
    gw = gateway_factory(
        {"api_keys": [{"token": "secret", "name": "t", "scopes": ["*"]}]}
    )
    client, _ = await _client(gw)
    async with client as c:
        assert (await c.get("/health")).status_code == 200
        assert (await c.get("/v1/metrics")).status_code == 200


async def test_env_api_key_shortcut_end_to_end(tmp_path, monkeypatch):
    """No YAML auth block + VOICEGW_API_KEY env → enforcement kicks in.

    This bypasses the shared factory so the env var survives into
    build_app's load_api_keys call.
    """
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "auth-env.db"))
    monkeypatch.setenv("VOICEGW_API_KEY", "from-env")
    path = _write_config(tmp_path, None)
    gw = Gateway(config_path=path)
    client, _ = await _client(gw)
    async with client as c:
        unauth = await c.post(
            "/v1/providers",
            json={"provider_id": "ollama-x", "provider_type": "ollama", "api_key": ""},
        )
        assert unauth.status_code == 401
        ok = await c.post(
            "/v1/providers",
            headers={"Authorization": "Bearer from-env"},
            json={"provider_id": "ollama-x", "provider_type": "ollama", "api_key": ""},
        )
        assert ok.status_code == 200


async def test_cors_origins_config_restricts(gateway_factory):
    """Preflight with a configured origin succeeds; disallowed origin
    gets no Access-Control-Allow-Origin header."""
    gw = gateway_factory(
        {
            "api_keys": [],
            "cors_origins": ["http://allowed.example"],
        }
    )
    client, _ = await _client(gw)
    async with client as c:
        allowed = await c.options(
            "/v1/providers",
            headers={
                "Origin": "http://allowed.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert allowed.headers.get("access-control-allow-origin") == (
            "http://allowed.example"
        )

        denied = await c.options(
            "/v1/providers",
            headers={
                "Origin": "http://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert denied.headers.get("access-control-allow-origin") != (
            "http://evil.example"
        )
