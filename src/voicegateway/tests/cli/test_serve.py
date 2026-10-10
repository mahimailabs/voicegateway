"""Tests for ``voicegw serve`` bind resolution."""

from __future__ import annotations

import pytest

from voicegateway.cli.serve_cli import _resolve_bind


class _ServeStub:
    """The schema's ServeConfig model may be passed directly."""

    host = "10.0.0.5"
    port = 7777


@pytest.mark.parametrize(
    ("serve_cfg", "flag_host", "flag_port", "expected"),
    [
        ({"host": "127.0.0.1", "port": 9123}, None, None, ("127.0.0.1", 9123)),
        ({"host": "127.0.0.1", "port": 9123}, "0.0.0.0", 8080, ("0.0.0.0", 8080)),
        ({}, None, None, ("0.0.0.0", 8080)),
        (None, None, None, ("0.0.0.0", 8080)),  # pre-v0.1.0 config, no serve:
        (_ServeStub(), None, None, ("10.0.0.5", 7777)),
        ({"port": "not-a-number"}, None, None, ("0.0.0.0", 8080)),
        ({"port": 0}, None, None, ("0.0.0.0", 8080)),
        ({"port": -1}, None, None, ("0.0.0.0", 8080)),
        ({"port": 65536}, None, None, ("0.0.0.0", 8080)),
        ({"port": 1}, None, None, ("0.0.0.0", 1)),
        ({"port": 65535}, None, None, ("0.0.0.0", 65535)),
        ({}, None, 70000, ("0.0.0.0", 8080)),  # explicit out-of-range flag
    ],
)
def test_resolve_bind(serve_cfg, flag_host, flag_port, expected):
    assert _resolve_bind(serve_cfg, host=flag_host, port=flag_port) == expected
