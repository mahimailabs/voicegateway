"""Input schemas for the providers MCP tool group."""

from __future__ import annotations

from voicegateway.schemas.mcp import StrictMcpInput


class ListProvidersInput(StrictMcpInput):
    """Input for list_providers."""


class GetProviderInput(StrictMcpInput):
    """Input for get_provider: requires the provider id."""

    provider_id: str


class TestProviderInput(StrictMcpInput):
    """Input for test_provider: requires the provider id."""

    provider_id: str


class AddProviderInput(StrictMcpInput):
    """Input for add_provider: managed provider write."""

    provider_id: str
    provider_type: str
    api_key: str = ""
    base_url: str | None = None


class VgAddProviderInput(StrictMcpInput):
    """Per-project provider key write."""

    project: str
    provider: str
    api_key: str
    base_url: str | None = None


class VgRemoveProviderInput(StrictMcpInput):
    """Remove a per-project provider key."""

    project: str
    provider: str


class VgListProvidersInput(StrictMcpInput):
    """List per-project provider keys."""

    project: str | None = None


class VgSetProviderKeyInput(StrictMcpInput):
    """Rotate an existing per-project provider key."""

    project: str
    provider: str
    api_key: str
    base_url: str | None = None


class VgTestProviderKeyInput(StrictMcpInput):
    """Sanity-check a per-project provider key."""

    project: str
    provider: str


class DeleteProviderInput(StrictMcpInput):
    """Input for delete_provider: requires confirm flag for destructive write."""

    provider_id: str
    confirm: bool = False


__all__ = [
    "AddProviderInput",
    "DeleteProviderInput",
    "GetProviderInput",
    "ListProvidersInput",
    "TestProviderInput",
    "VgAddProviderInput",
    "VgListProvidersInput",
    "VgRemoveProviderInput",
    "VgSetProviderKeyInput",
    "VgTestProviderKeyInput",
]
