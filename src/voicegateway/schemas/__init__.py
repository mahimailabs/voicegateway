"""Pydantic request/response schemas for the HTTP API and MCP.

Distinguished from :mod:`voicegateway.models`, which holds persistence
dataclasses, and :mod:`voicegateway.core.schema`, which holds the
``voicegw.yaml`` validation schema. As the API surface grows, request
and response shapes land here as ``schemas/<domain>.py``.
"""

__all__: list[str] = []
