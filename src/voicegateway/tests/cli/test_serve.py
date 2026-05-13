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


def test_resolve_bind_clamps_out_of_range_port_to_default(capsys):
    """Out-of-range ports (negative, zero, > 65535) fall back to the
    v0.0.5 default rather than reaching ``uvicorn.run``. uvicorn accepts
    garbage at ``Config`` time and only fails later with an opaque
    socket error, so the helper substitutes early and prints a yellow
    warning so the operator sees what happened.
    """
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
    """An explicit ``--port 70000`` reaches ``_resolve_bind`` as the
    ``port`` argument; the same clamp applies so a typed CLI value
    cannot bypass the range guard either.
    """
    _, port = _resolve_bind({}, host=None, port=70000)
    assert port == 8080
