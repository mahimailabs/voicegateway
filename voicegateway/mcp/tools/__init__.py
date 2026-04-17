"""MCP tool aggregation.

Each tool file exports a list of ``ToolDef`` definitions, which this module
concatenates into ``ALL_TOOLS`` for registration with the MCP server.
"""

from voicegateway.mcp.tools.base import ToolDef
from voicegateway.mcp.tools.models import MODEL_TOOLS
from voicegateway.mcp.tools.observability import OBSERVABILITY_TOOLS
from voicegateway.mcp.tools.projects import PROJECT_TOOLS
from voicegateway.mcp.tools.providers import PROVIDER_TOOLS

ALL_TOOLS: list[ToolDef] = [
    *OBSERVABILITY_TOOLS,
    *PROVIDER_TOOLS,
    *MODEL_TOOLS,
    *PROJECT_TOOLS,
]

__all__ = ["ALL_TOOLS", "ToolDef"]
