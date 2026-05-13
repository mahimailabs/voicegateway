"""Pydantic schema for Phase 3 streaming fixtures."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FixtureMetadata(BaseModel):
    """Fixed metadata for a recorded fixture."""

    model_config = ConfigDict(extra="allow")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    modality: Literal["stt", "llm", "tts"]
    mode: Literal["batch", "stream"]
    recorded_at: datetime
    recorded_by: str = Field(min_length=1)
    voicegateway_version: str = Field(min_length=1)


class FixtureChunk(BaseModel):
    """One chunk of a recorded response stream."""

    model_config = ConfigDict(extra="forbid")

    chunk_index: int = Field(ge=0)
    received_at_ms: int = Field(ge=0)
    data: dict[str, Any] | list[Any] | str


class StreamingFixture(BaseModel):
    """Top-level fixture schema."""

    model_config = ConfigDict(extra="forbid")

    metadata: FixtureMetadata
    request: dict[str, Any]
    response_stream: list[FixtureChunk]
    provider_reported_usage: dict[str, Any]
    expected_cost_usd: Decimal

    @field_validator("response_stream")
    @classmethod
    def _chunk_indices_are_sequential(cls, v: list[FixtureChunk]) -> list[FixtureChunk]:
        for position, chunk in enumerate(v):
            if chunk.chunk_index != position:
                raise ValueError(
                    f"response_stream[{position}].chunk_index is "
                    f"{chunk.chunk_index}; expected {position}. "
                    "Chunks must be in order with zero-based "
                    "contiguous indices."
                )
        return v

    @field_validator("expected_cost_usd")
    @classmethod
    def _expected_cost_non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError(f"expected_cost_usd must be non-negative, got {v}")
        return v
