"""SQLite-backed ``MetricsClient`` for v0.1.1 Local mode.

Wraps the existing :class:`voicegateway.storage.sqlite.SQLiteStorage`
read methods so the TUI can inspect a gateway's recent activity even
when the daemon is down (postmortem use case from the Refinery).
LocalClient does NOT bypass the storage layer with raw SQL: every
read routes through SQLiteStorage so a future schema change has one
adapter to update.

Polling cadence defaults to 5 seconds per locked decision 2: SQLite
reads are cheap, but TUI redraws are not free on a battery-powered
laptop or over a thin SSH session, and "is my agent making calls"
is rarely a sub-second question in postmortem mode. The Typer
``--poll`` flag overrides.

Write paths (only ``test_provider`` today) raise
:class:`LocalModeUnsupportedError` with a ``feature`` attribute so
the Providers screen renders a "requires daemon" hint cleanly.
"""

from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any

from voicegateway.cli.tui.data.exceptions import LocalModeUnsupportedError
from voicegateway.storage.sqlite import SQLiteStorage


class LocalClient:
    """SQLite-backed :class:`MetricsClient` for daemon-down access.

    Constructor arguments are keyword-only. ``db_path`` points at the
    voicegw SQLite file (default location is
    ``~/.config/voicegateway/voicegw.db``; the factory resolves it
    from the loaded config). ``storage`` is an injection seam for
    tests: pass an in-memory or fixture-backed
    :class:`SQLiteStorage` instance to drive the methods without
    touching the filesystem.

    Attributes:
        poll_seconds: refresh cadence in seconds. The Sessions /
            Logs screens read this when registering refresh timers.
            Default 5.0 per locked decision 2.
    """

    def __init__(
        self,
        *,
        db_path: str | Path,
        poll_seconds: float = 5.0,
        storage: SQLiteStorage | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self.poll_seconds = poll_seconds
        # SQLiteStorage manages aiosqlite connections per-call, so we
        # construct it once and reuse; no global connection to leak.
        self._storage = storage if storage is not None else SQLiteStorage(self._db_path)

    # -- lifecycle ---------------------------------------------------

    async def aclose(self) -> None:
        """Close any raw SQLite handles still open from in-flight polls.

        Provided for symmetry with :class:`HttpClient.aclose` so the
        TUIApp's on_unmount can release both backends through a single
        dispatch path.
        """
        await self._storage.aclose()

    async def __aenter__(self) -> LocalClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # -- MetricsClient methods --------------------------------------

    async def list_sessions(
        self,
        *,
        limit: int = 100,
        project: str | None = None,
        order_by: str = "started_at_desc",
    ) -> list[dict[str, Any]]:
        """Delegate to ``SQLiteStorage.list_sessions``."""
        return await self._storage.list_sessions(
            limit=limit, project=project, order_by=order_by
        )

    async def get_session_detail(self, session_id: str) -> dict[str, Any] | None:
        """Delegate to ``SQLiteStorage.get_session``.

        Storage returns ``None`` when the id is unknown; the Protocol
        passes that through so the drill-in modal can render an
        "unknown session" hint without an exception.
        """
        return await self._storage.get_session(session_id)

    async def list_costs(
        self,
        *,
        period: str = "today",
        project: str | None = None,
        include_pricing_source: bool = False,
    ) -> dict[str, Any]:
        """Combine total + per-modality so the response shape matches
        the daemon's ``/v1/costs?per_modality=true`` (the Costs tab
        consumes one shape regardless of which client is live).
        """
        summary = await self._storage.get_cost_summary(
            period=period,
            project=project,
            include_pricing_source=include_pricing_source,
        )
        by_modality = await self._storage.get_cost_by_modality(
            period=period, project=project
        )
        result: dict[str, Any] = dict(summary) if isinstance(summary, dict) else {}
        # ``get_cost_by_modality`` may return either an inline-keys
        # dict (``{"stt": ..., "llm": ..., "tts": ...}``) or a wrapped
        # ``{"by_modality": {...}}`` shape depending on the storage
        # version. Normalize to the wrapped shape the Costs screen
        # expects.
        if isinstance(by_modality, dict) and "by_modality" in by_modality:
            result["by_modality"] = by_modality["by_modality"]
        else:
            result["by_modality"] = by_modality
        return result

    async def list_logs(
        self,
        *,
        limit: int = 100,
        project: str | None = None,
        modality: str | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate to ``SQLiteStorage.get_recent_requests``.

        Storage returns the rows newest-first already; the Logs screen
        polls and de-dupes by request id, so no extra work here.
        """
        return await self._storage.get_recent_requests(
            limit=limit, modality=modality, project=project
        )

    async def list_providers(
        self,
        *,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate to ``SQLiteStorage.list_managed_providers``.

        Storage returns every row; we filter on ``project`` here when
        given so the call site does not need to. Project filter
        matches the v0.0.5 ``managed_providers.project`` column.
        """
        rows = await self._storage.list_managed_providers()
        if project is None:
            return rows
        return [row for row in rows if row.get("project") == project]

    async def test_provider(self, provider_id: str) -> dict[str, Any]:
        """Always raises ``LocalModeUnsupportedError(feature='test_provider')``.

        The daemon is the only path that holds the upstream provider
        sessions and rate-limit budget; reproducing that machinery in
        Local mode would duplicate state and risk drift. The
        Providers screen catches the error and surfaces the
        ``str(err)`` payload as a one-line hint.
        """
        raise LocalModeUnsupportedError(feature="test_provider")


__all__ = ["LocalClient"]
