"""Data-layer Protocol for the TUI."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MetricsClient(Protocol):
    """Backend-agnostic interface every TUI screen consumes."""

    async def list_sessions(
        self,
        *,
        limit: int = 100,
        project: str | None = None,
        order_by: str = "started_at_desc",
    ) -> list[dict[str, Any]]:
        """Return recent voice sessions for the Sessions tab."""
        ...

    async def get_session_detail(self, session_id: str) -> dict[str, Any] | None:
        """Return one session plus its per-turn rows for the
        drill-in modal pushed by Enter on a focused row, or ``None``
        when the id does not match a known session.
        """
        ...

    async def list_costs(
        self,
        *,
        period: str = "today",
        project: str | None = None,
        include_pricing_source: bool = False,
    ) -> dict[str, Any]:
        """Return total + per-modality cost summary for the Costs tab."""
        ...

    async def list_logs(
        self,
        *,
        limit: int = 100,
        project: str | None = None,
        modality: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent request rows for the Logs tab tail view."""
        ...

    async def list_providers(
        self,
        *,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return configured providers for the Providers tab."""
        ...

    async def test_provider(self, provider_id: str) -> dict[str, Any]:
        """Trigger a live key-test against ``provider_id``'s upstream
        and return the result row.
        """
        ...


__all__ = ["MetricsClient"]
