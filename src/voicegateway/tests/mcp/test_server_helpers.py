"""Unit tests for the small helpers in voicegateway.mcp.server.

`_format_tool_error` and `_format_tool_result` are leaf helpers that
serialize tool outputs into the MCP transport-friendly JSON shape.
The integration tests in `test_transport.py` exercise them through the
full server, but a focused unit test surfaces breakage faster and
documents the contract clearly.
"""

from __future__ import annotations

import json

from voicegateway.mcp.errors import MCPToolError, ProviderNotFoundError
from voicegateway.mcp.server import _format_tool_error, _format_tool_result


def test_format_tool_error_with_mcp_tool_error_uses_to_dict() -> None:
    """An MCPToolError serializes via its to_dict envelope."""
    exc = ProviderNotFoundError(
        "no such provider 'foobar'", details={"name": "foobar"}
    )
    payload = json.loads(_format_tool_error(exc))
    assert payload["error"]["code"] == "PROVIDER_NOT_FOUND"
    assert payload["error"]["message"] == "no such provider 'foobar'"
    assert payload["error"]["details"] == {"name": "foobar"}


def test_format_tool_error_with_unknown_exception_uses_internal_envelope() -> None:
    """Non-MCPToolError exceptions get the INTERNAL_ERROR envelope."""
    exc = ValueError("something went wrong")
    payload = json.loads(_format_tool_error(exc))
    assert payload["error"]["code"] == "INTERNAL_ERROR"
    assert payload["error"]["message"] == "something went wrong"
    assert payload["error"]["details"] == {}


def test_format_tool_error_falls_back_to_class_name_when_message_empty() -> None:
    """An exception with an empty str() falls back to its class name."""
    class _CustomBoom(Exception):
        pass

    exc = _CustomBoom()
    payload = json.loads(_format_tool_error(exc))
    assert payload["error"]["message"] == "_CustomBoom"


def test_format_tool_error_with_subclass_of_mcp_tool_error() -> None:
    """Direct MCPToolError subclasses surface their own error_code."""
    exc = MCPToolError("custom", error_code="CUSTOM_CODE", details={"x": 1})
    payload = json.loads(_format_tool_error(exc))
    assert payload["error"]["code"] == "CUSTOM_CODE"
    assert payload["error"]["details"] == {"x": 1}


def test_format_tool_result_serializes_dict() -> None:
    """A dict result round-trips through json.dumps cleanly."""
    payload = json.loads(_format_tool_result({"a": 1, "b": [2, 3]}))
    assert payload == {"a": 1, "b": [2, 3]}


def test_format_tool_result_uses_default_str_for_non_serializable() -> None:
    """`default=str` means Decimals, dates, and similar coerce to str."""
    from decimal import Decimal

    payload = json.loads(_format_tool_result({"cost": Decimal("0.0123")}))
    assert payload == {"cost": "0.0123"}
