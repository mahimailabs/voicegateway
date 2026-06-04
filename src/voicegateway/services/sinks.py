"""Write sinks: the seam between record producers and where rows land.

A ``Sink`` is the narrow write interface every telemetry producer (the
inference middleware and the ``attach()`` capture pipeline) targets. In
single-node mode the sink is :class:`LocalSqliteSink`, which writes to the
embedded SQLite ``StorageService``. In fleet mode it is the
``RemoteCollectorSink`` (added in a later build step), which batches rows
and pushes them to a collector. Producers stay identical; only the sink
swaps.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from voicegateway.models.request_model import RequestRecord
    from voicegateway.services.storage_service import StorageService


@runtime_checkable
class Sink(Protocol):
    """Narrow write target for request records.

    ``log_request`` persists one record. ``flush`` drains any buffered
    writes (a no-op for synchronous sinks). ``aclose`` releases resources.
    """

    async def log_request(self, record: RequestRecord) -> None: ...

    async def flush(self) -> None: ...

    async def aclose(self) -> None: ...


class LocalSqliteSink:
    """Default single-node sink: writes through the embedded StorageService.

    Delegates ``log_request`` and the session-finalization hooks to the
    wrapped storage so ``CostTracker.close_session`` (which resolves
    ``finalize_session_*`` by ``getattr``) keeps working unchanged. ``flush``
    is a no-op because each SQLite write commits on its own.
    """

    def __init__(self, storage: StorageService) -> None:
        self._storage = storage

    async def log_request(self, record: RequestRecord) -> None:
        await self._storage.log_request(record)

    async def flush(self) -> None:
        return None

    async def aclose(self) -> None:
        await self._storage.aclose()

    async def finalize_session_metrics(self, session_id: str) -> None:
        await self._storage.finalize_session_metrics(session_id)

    async def finalize_session_replay(self, session_id: str) -> None:
        await self._storage.finalize_session_replay(session_id)


__all__ = ["LocalSqliteSink", "Sink"]
