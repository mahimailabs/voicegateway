"""Request/response logging middleware."""

from __future__ import annotations

import logging

logger = logging.getLogger("gateway.requests")


class RequestLogger:
    """Logs inference requests and responses."""

    def __init__(self, level: int = logging.INFO):
        self._level = level

    def log_request(self, model_id: str, modality: str, **kwargs) -> None:
        """Log an incoming request."""
        logger.log(self._level, f"[{modality.upper()}] {model_id}")

    def log_response(
        self,
        model_id: str,
        modality: str,
        latency_ms: float,
        cost_usd: float,
        status: str = "success",
    ) -> None:
        """Log a completed response."""
        logger.log(
            self._level,
            f"[{modality.upper()}] {model_id} → {status} "
            f"({latency_ms:.0f}ms, ${cost_usd:.6f})",
        )

    def log_fallback(
        self, original: str, fallback: str, reason: str
    ) -> None:
        """Log a fallback event."""
        logger.warning(
            f"[FALLBACK] {original} → {fallback} (reason: {reason})"
        )

    def log_error(self, model_id: str, error: str) -> None:
        """Log an error."""
        logger.error(f"[ERROR] {model_id}: {error}")
