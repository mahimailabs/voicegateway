"""MCP tool aggregation."""

from voicegateway.server.mcp.tools.base import ToolDef
from voicegateway.server.mcp.tools.models import MODEL_TOOLS
from voicegateway.server.mcp.tools.observability import OBSERVABILITY_TOOLS
from voicegateway.server.mcp.tools.projects import PROJECT_TOOLS
from voicegateway.server.mcp.tools.providers import PROVIDER_TOOLS

ALL_TOOLS: list[ToolDef] = [
    *OBSERVABILITY_TOOLS,
    *PROVIDER_TOOLS,
    *MODEL_TOOLS,
    *PROJECT_TOOLS,
]

__all__ = ["ALL_TOOLS", "ToolDef"]
