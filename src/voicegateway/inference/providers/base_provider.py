"""Abstract base class for all provider implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseProvider(ABC):
    """Base class for all provider implementations."""

    @abstractmethod
    def create_stt(self, model: str, **kwargs: Any) -> Any:
        """Create an STT instance. Return None if provider doesn't support STT."""
        ...

    @abstractmethod
    def create_llm(self, model: str, **kwargs: Any) -> Any:
        """Create an LLM instance. Return None if provider doesn't support LLM."""
        ...

    @abstractmethod
    def create_tts(self, model: str, voice: str | None = None, **kwargs: Any) -> Any:
        """Create a TTS instance. Return None if provider doesn't support TTS."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the provider is reachable."""
        ...

    def _unsupported(self, modality: str) -> None:
        """Raise error for unsupported modality."""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support {modality}"
        )
