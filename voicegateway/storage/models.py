"""Data models for storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RequestRecord:
    """A single inference request record."""

    id: str
    timestamp: float
    modality: str  # 'stt', 'llm', 'tts'
    model_id: str  # 'deepgram/nova-3'
    provider: str  # 'deepgram'
    project: str = "default"  # project ID the request is tagged with
    input_units: float = 0.0  # minutes (stt), tokens (llm), characters (tts)
    output_units: float = 0.0  # tokens (llm)
    cost_usd: float = 0.0
    ttfb_ms: float | None = None
    total_latency_ms: float | None = None
    status: str = "success"  # 'success', 'error', 'fallback'
    fallback_from: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
