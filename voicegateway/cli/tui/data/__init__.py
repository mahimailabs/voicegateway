"""Data-layer Protocol for the v0.1.1 TUI.

Every TUI screen consumes the abstract :class:`MetricsClient` and
never knows whether the live backend is the daemon HTTP API or the
SQLite file on disk. Mode selection happens at launch via the
``--local`` flag and the factory in ``factory.py``.

Two implementations land in sibling modules during Phase 1:

- ``http.py`` -- ``HttpClient`` for Gateway mode (wraps
  ``httpx.AsyncClient`` against the daemon's ``/v1/*`` endpoints,
  polling at 1 s by default).
- ``local.py`` -- ``LocalClient`` for Local mode (wraps the existing
  ``voicegateway.storage.sqlite.SQLiteStorage`` read methods,
  polling at 5 s by default).

Write methods raise :class:`LocalModeUnsupportedError` (with a
``feature`` attribute) on the local client; the http client carries
them out against the daemon. Read methods are valid in both modes.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MetricsClient(Protocol):
    """Backend-agnostic interface every v0.1.1 TUI screen consumes.

    All methods are ``async`` so the Textual event loop can keep
    rendering while a poll round-trip is in flight. The factory in
    ``voicegateway.cli.tui.data.factory`` returns either ``HttpClient``
    or ``LocalClient`` depending on the ``--local`` flag; both
    structurally satisfy this Protocol.

    Locked decision 5 (see ``.agents/design.md`` section 4): when
    ``--local`` is set the factory always returns ``LocalClient``
    regardless of whether the daemon is reachable, so a stale shell
    alias cannot silently re-route writes through the daemon. The
    ``[Local mode]`` chip in the header is the visible signal.
    """

    async def list_sessions(
        self,
        *,
        limit: int = 100,
        project: str | None = None,
        order_by: str = "started_at_desc",
    ) -> list[dict[str, Any]]:
        """Return recent voice sessions for the Sessions tab.

        ``order_by`` accepts ``started_at_desc`` (newest first;
        default) and ``total_cost_usd_desc`` (most expensive first)
        so the screen's ``s`` toggle can flip between the two.
        ``limit`` caps the row count; ``project`` filters to one
        project when given.
        """
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
        """Return total + per-modality cost summary for the Costs tab.

        ``period`` accepts ``today``, ``this_week``, ``this_month``.
        ``include_pricing_source=True`` makes the response carry
        ``pricing_source_url`` and ``pricing_source_date`` keys so the
        screen's ``as of X`` freshness indicator can render when the
        upstream genai-prices stamp is older than 24 h.
        """
        ...

    async def list_logs(
        self,
        *,
        limit: int = 100,
        project: str | None = None,
        modality: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent request rows for the Logs tab tail view.

        The screen polls this method, de-dupes by request id against
        the rows it already rendered, then appends the new ones at
        the bottom. ``modality`` filters to one of ``stt`` / ``llm``
        / ``tts`` when given.
        """
        ...

    async def list_providers(
        self,
        *,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return configured providers for the Providers tab.

        Each row carries the last-known test status (``ok`` / ``fail``
        / ``untested``) which the green/red indicator reads. ``project``
        filters to per-project provider entries when given (matches
        the v0.0.5 per-project provider key resolution; see the
        ``managed_providers.project`` column).
        """
        ...

    async def test_provider(self, provider_id: str) -> dict[str, Any]:
        """Trigger a live key-test against ``provider_id``'s upstream
        and return the result row.

        Write op. ``LocalClient.test_provider`` raises
        ``LocalModeUnsupportedError(feature='test_provider')`` so the
        Providers screen's ``t`` shortcut can render a one-line
        ``requires daemon`` message instead of silently failing.
        ``HttpClient.test_provider`` ``POST``s to the daemon's
        ``/v1/providers/{id}/test`` endpoint.
        """
        ...


__all__ = ["MetricsClient"]
