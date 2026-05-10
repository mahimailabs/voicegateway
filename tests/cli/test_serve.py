"""Tests for ``voicegw serve`` bind resolution.

Pins Codex P1.2: when the user runs the v0.1.0 daemon (which executes
bare ``voicegw serve`` from the platform service unit), the bind host
and port must come from ``serve.host`` / ``serve.port`` in voicegw.yaml
so the wizard-selected port survives. Explicit ``--host`` / ``--port``
flags still win for ad-hoc invocations.
"""

from __future__ import annotations

from voicegateway.cli.serve import _resolve_bind


def test_resolve_bind_uses_config_when_flags_absent():
    serve_cfg = {"host": "127.0.0.1", "port": 9123}
    host, port = _resolve_bind(serve_cfg, host=None, port=None)
    assert host == "127.0.0.1"
    assert port == 9123


def test_resolve_bind_explicit_flags_override_config():
    serve_cfg = {"host": "127.0.0.1", "port": 9123}
    host, port = _resolve_bind(serve_cfg, host="0.0.0.0", port=8080)
    assert host == "0.0.0.0"
    assert port == 8080


def test_resolve_bind_falls_back_to_v005_defaults_when_unset():
    host, port = _resolve_bind({}, host=None, port=None)
    assert host == "0.0.0.0"
    assert port == 8080


def test_resolve_bind_handles_missing_serve_section():
    """Pre-v0.1.0 configs lack ``serve:``; the dataclass default is an
    empty dict and the resolver must not raise.
    """
    host, port = _resolve_bind(None, host=None, port=None)
    assert host == "0.0.0.0"
    assert port == 8080


def test_resolve_bind_accepts_pydantic_like_serve_object():
    """Some callers may pass the schema's ``ServeConfig`` model directly
    instead of the dataclass dict. The helper should read attributes too.
    """

    class _ServeStub:
        host = "10.0.0.5"
        port = 7777

    host, port = _resolve_bind(_ServeStub(), host=None, port=None)
    assert host == "10.0.0.5"
    assert port == 7777


def test_resolve_bind_tolerates_garbled_port_value():
    """A non-int port in the config (handwritten yaml) should not crash;
    fall back to the v0.0.5 default.
    """
    host, port = _resolve_bind({"port": "not-a-number"}, host=None, port=None)
    assert host == "0.0.0.0"
    assert port == 8080
