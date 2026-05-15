"""Tests for ``voicegw serve`` bind resolution."""

from __future__ import annotations

from voicegateway.cli.serve_cli import _resolve_bind


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
    """Pre-v0.1.0 configs lack ``serve:``; the dataclass default is an"""
    host, port = _resolve_bind(None, host=None, port=None)
    assert host == "0.0.0.0"
    assert port == 8080


def test_resolve_bind_accepts_pydantic_like_serve_object():
    """Some callers may pass the schema's ``ServeConfig`` model directly"""

    class _ServeStub:
        host = "10.0.0.5"
        port = 7777

    host, port = _resolve_bind(_ServeStub(), host=None, port=None)
    assert host == "10.0.0.5"
    assert port == 7777


def test_resolve_bind_tolerates_garbled_port_value():
    """A non-int port in the config (handwritten yaml) should not crash;"""
    host, port = _resolve_bind({"port": "not-a-number"}, host=None, port=None)
    assert host == "0.0.0.0"
    assert port == 8080


def test_resolve_bind_clamps_out_of_range_port_to_default(capsys):
    """Out-of-range ports (negative, zero, > 65535) fall back to the"""
    for bad in (0, -1, 70000, 65536):
        host, port = _resolve_bind({"port": bad}, host=None, port=None)
        assert host == "0.0.0.0"
        assert port == 8080, f"port={bad} did not clamp to default"

    # Boundary values are accepted as-is.
    _, port = _resolve_bind({"port": 1}, host=None, port=None)
    assert port == 1
    _, port = _resolve_bind({"port": 65535}, host=None, port=None)
    assert port == 65535


def test_resolve_bind_clamps_out_of_range_explicit_flag():
    """An explicit ``--port 70000`` reaches ``_resolve_bind`` as the"""
    _, port = _resolve_bind({}, host=None, port=70000)
    assert port == 8080
