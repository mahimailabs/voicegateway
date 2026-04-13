"""Fallback chain logic for automatic model failover."""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


class FallbackError(Exception):
    """Raised when all models in the fallback chain have failed."""

    def __init__(self, chain: list[str], errors: list[tuple[str, Exception]]):
        self.chain = chain
        self.errors = errors
        details = "; ".join(f"{m}: {e}" for m, e in errors)
        super().__init__(
            f"All models in fallback chain failed. Chain: {chain}. Errors: {details}"
        )


class FallbackChain:
    """Manages fallback chains for a given modality.

    If the primary model fails (timeout, rate limit, error),
    automatically tries the next model in the chain.
    """

    def __init__(
        self,
        chain: list[str],
        resolver: Callable[[str, str], Any],
        modality: str,
        on_fallback: Callable[[str, str, str], None] | None = None,
    ):
        """Initialize fallback chain.

        Args:
            chain: Ordered list of model IDs to try.
            resolver: Function(model_id, modality) that creates the instance.
            modality: "stt", "llm", or "tts".
            on_fallback: Optional callback(original, fallback, reason) for logging.
        """
        self._chain = chain
        self._resolver = resolver
        self._modality = modality
        self._on_fallback = on_fallback

    def resolve(self, **kwargs: Any) -> Any:
        """Try each model in the chain until one succeeds.

        Returns the first successfully created instance.

        Raises:
            FallbackError: If all models fail.
        """
        errors: list[tuple[str, Exception]] = []

        for i, model_id in enumerate(self._chain):
            try:
                instance = self._resolver(model_id, self._modality, **kwargs)

                if i > 0:
                    primary = self._chain[0]
                    reason = str(errors[-1][1]) if errors else "unknown"
                    logger.warning(
                        f"Fallback triggered: {primary} → {model_id} (reason: {reason})"
                    )
                    if self._on_fallback:
                        self._on_fallback(primary, model_id, reason)

                return instance

            except Exception as e:
                logger.debug(f"Model {model_id} failed: {e}")
                errors.append((model_id, e))
                continue

        raise FallbackError(self._chain, errors)

    @property
    def primary(self) -> str:
        """Return the primary (first) model in the chain."""
        return self._chain[0]

    @property
    def chain(self) -> list[str]:
        """Return the full fallback chain."""
        return list(self._chain)
