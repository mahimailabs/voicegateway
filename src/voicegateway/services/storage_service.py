"""SQLite storage facade aggregating per-domain service objects."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database
from voicegateway.models.request_model import RequestRecord
from voicegateway.services.billing_service import BillingService
from voicegateway.services.cost_service import CostService
from voicegateway.services.latency_service import LatencyService
from voicegateway.services.managed_config_service import ManagedConfigService
from voicegateway.services.request_log_service import RequestLogService
from voicegateway.services.session_service import SessionService

_DEFAULT_PERCENTILES: list[float] = [50.0, 95.0, 99.0]

_logger = logging.getLogger(__name__)


class StorageService:
    """Aggregates per-domain services over one SQLite database."""

    def __init__(self, db_path: str | Path) -> None:
        # Database.__init__ quiets the noisy aiosqlite/alembic loggers now, so this
        # facade no longer needs its own call.
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        cfg = GatewayConfig(cost_tracking={"db_path": str(self._db_path)})
        self._conn = Database(cfg)
        self._initialized = False
        # Serialize concurrent _ensure_initialized calls so alembic's
        # bootstrap of the alembic_version table never races itself.
        self._init_lock = asyncio.Lock()
        self._request_log_service = RequestLogService(self._conn)
        self._cost_service = CostService(self._conn)
        self._billing_service = BillingService(self._conn)
        self._latency_service = LatencyService(self._conn)
        self._session_service = SessionService(self._conn)
        self._managed_config_service = ManagedConfigService(self._conn)

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:  # double-checked after lock
                return
            await self._conn.run_migrations()
            self._initialized = True

    async def aclose(self) -> None:
        """Dispose the underlying engine."""
        await self._conn.dispose()

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    async def log_audit_event(
        self,
        entity_type: str,
        entity_id: str,
        action: str,
        changes: dict[str, Any] | None = None,
        source: str = "api",
    ) -> None:
        """Delegate to RequestLogService.log_audit_event."""
        await self._ensure_initialized()
        await self._request_log_service.log_audit_event(
            entity_type, entity_id, action, changes, source
        )

    async def get_audit_log(
        self,
        limit: int = 50,
        entity_type: str | None = None,
        entity_id: str | None = None,
        action: str | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate to RequestLogService.get_audit_log."""
        await self._ensure_initialized()
        return await self._request_log_service.get_audit_log(
            limit, entity_type, entity_id, action
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def log_request(self, record: RequestRecord) -> None:
        """Delegate to RequestLogService.log_request."""
        await self._ensure_initialized()
        await self._request_log_service.log_request(record)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_cost_summary(
        self,
        period: str = "today",
        project: str | None = None,
        include_pricing_source: bool = False,
        start_ts: float | None = None,
        end_ts: float | None = None,
        tenant: str | None = None,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """Delegate to CostService.get_summary."""
        await self._ensure_initialized()
        return await self._cost_service.get_summary(
            period=period,
            project=project,
            include_pricing_source=include_pricing_source,
            start_ts=start_ts,
            end_ts=end_ts,
            tenant=tenant,
            agent=agent,
        )

    async def get_cost_by_project(
        self,
        period: str = "today",
        start_ts: float | None = None,
        end_ts: float | None = None,
        tenant: str | None = None,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """Delegate to CostService.get_by_project."""
        await self._ensure_initialized()
        return await self._cost_service.get_by_project(
            period=period,
            start_ts=start_ts,
            end_ts=end_ts,
            tenant=tenant,
            agent=agent,
        )

    async def get_cost_by_day(
        self,
        period: str = "week",
        project: str | None = None,
        start_ts: float | None = None,
        end_ts: float | None = None,
        tenant: str | None = None,
        agent: str | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate to CostService.get_by_day."""
        await self._ensure_initialized()
        return await self._cost_service.get_by_day(
            period=period,
            project=project,
            start_ts=start_ts,
            end_ts=end_ts,
            tenant=tenant,
            agent=agent,
        )

    async def get_cost_by_modality(
        self,
        period: str = "today",
        project: str | None = None,
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> dict[str, Any]:
        """Delegate to CostService.get_by_modality."""
        await self._ensure_initialized()
        return await self._cost_service.get_by_modality(
            period=period, project=project, start_ts=start_ts, end_ts=end_ts
        )

    async def get_billable_usage(
        self,
        period: str = "month",
        start_ts: float | None = None,
        end_ts: float | None = None,
        project: str | None = None,
        tenant: str | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate to BillingService.get_billable_usage."""
        await self._ensure_initialized()
        return await self._billing_service.get_billable_usage(
            period=period,
            start_ts=start_ts,
            end_ts=end_ts,
            project=project,
            tenant=tenant,
        )

    async def get_tenant_line_items(
        self,
        tenant: str,
        period: str = "month",
        start_ts: float | None = None,
        end_ts: float | None = None,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate to BillingService.get_tenant_line_items."""
        await self._ensure_initialized()
        return await self._billing_service.get_tenant_line_items(
            tenant=tenant,
            period=period,
            start_ts=start_ts,
            end_ts=end_ts,
            project=project,
        )

    async def get_latency_stats(
        self,
        period: str = "today",
        project: str | None = None,
        percentiles: list[float] | None = None,
        tenant: str | None = None,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """Delegate to LatencyService.get_stats."""
        await self._ensure_initialized()
        return await self._latency_service.get_stats(
            period=period,
            project=project,
            percentiles=percentiles,
            tenant=tenant,
            agent=agent,
        )

    async def get_latency_samples(
        self,
        period: str = "today",
        project: str | None = None,
        modality: str | None = None,
    ) -> tuple[list[float], list[float]]:
        """Delegate to LatencyService.get_samples."""
        await self._ensure_initialized()
        return await self._latency_service.get_samples(
            period=period, project=project, modality=modality
        )

    async def get_requests_in_window(
        self,
        start_ts: float | None = None,
        end_ts: float | None = None,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate to RequestLogService.get_requests_in_window."""
        await self._ensure_initialized()
        return await self._request_log_service.get_requests_in_window(
            start_ts, end_ts, project
        )

    async def get_recent_requests(
        self,
        limit: int = 100,
        modality: str | None = None,
        project: str | None = None,
        tenant: str | None = None,
        agent: str | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate to RequestLogService.get_recent_requests."""
        await self._ensure_initialized()
        return await self._request_log_service.get_recent_requests(
            limit, modality, project, tenant, agent
        )

    async def get_requests_for_room(
        self, room: str, since: float | None = None
    ) -> list[dict[str, Any]]:
        """Delegate to RequestLogService.get_requests_for_room."""
        await self._ensure_initialized()
        return await self._request_log_service.get_requests_for_room(room, since)

    async def get_project_stats(self, project: str) -> dict[str, Any]:
        """Delegate to CostService.get_project_stats."""
        await self._ensure_initialized()
        return await self._cost_service.get_project_stats(project)

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    async def list_sessions(
        self,
        limit: int = 100,
        project: str | None = None,
        order_by: str = "started_at_desc",
        tenant: str | None = None,
        agent: str | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate to SessionService.list_sessions."""
        await self._ensure_initialized()
        return await self._session_service.list_sessions(
            limit=limit,
            project=project,
            order_by=order_by,
            tenant=tenant,
            agent=agent,
        )

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Delegate to SessionService.get_session."""
        await self._ensure_initialized()
        return await self._session_service.get_session(session_id)

    async def finalize_session_metrics(self, session_id: str) -> None:
        """Delegate to SessionService.finalize_metrics."""
        await self._ensure_initialized()
        await self._session_service.finalize_metrics(session_id)

    async def finalize_session_replay(self, session_id: str) -> None:
        """Delegate to SessionService.finalize_replay."""
        await self._ensure_initialized()
        await self._session_service.finalize_replay(session_id)

    async def read_correlation_rate(
        self,
        *,
        project: str | None = None,
        since: str | None = None,
        warn_threshold: float | None = None,
    ) -> dict[str, Any]:
        """How often the sessions <-> calls join resolves, as a dict.

        The denominator is sessions that HAD a room and so should have joined;
        sessions with no room (web, Pipecat) are reported as ``no_room`` and
        excluded from both sides, and ``rate`` is None when that denominator is
        empty. ``status`` is one of ``session_repository.CORRELATION_STATUSES``,
        against ``warn_threshold`` (default
        ``session_repository.CORRELATION_WARN_THRESHOLD``).
        """
        import dataclasses

        from voicegateway.repository import session_repository

        await self._ensure_initialized()
        async with self._conn.session() as db:
            row = await session_repository.read_correlation_rate(
                db, project=project, since=since, warn_threshold=warn_threshold
            )
        return dataclasses.asdict(row)

    # ------------------------------------------------------------------
    # Transcripts
    # ------------------------------------------------------------------

    async def write_transcript(
        self,
        session_id: str,
        turns: list[tuple[str, str]],
        *,
        tenant_id: str | None = None,
    ) -> int:
        """Replace a session's transcript with ``turns`` ((role, text) list)."""
        from voicegateway.repository import transcript_turns_repository

        await self._ensure_initialized()
        async with self._conn.session() as db:
            return await transcript_turns_repository.create_transcript_bulk(
                db, session_id, turns, tenant_id=tenant_id
            )

    async def get_transcript(self, session_id: str) -> list[dict[str, Any]]:
        """Return a session's transcript turns, ordered, as dicts."""
        import dataclasses

        from voicegateway.repository import transcript_turns_repository

        await self._ensure_initialized()
        async with self._conn.session() as db:
            rows = await transcript_turns_repository.list_transcript_by_session(
                db, session_id
            )
        return [dataclasses.asdict(r) for r in rows]

    # ------------------------------------------------------------------
    # Calls + call legs (written by paths that never touch inference)
    # ------------------------------------------------------------------

    async def upsert_call(
        self,
        *,
        origin: str,
        room_sid: str | None = None,
        attempt_id: str | None = None,
        room_name: str | None = None,
        run_id: str | None = None,
        project: str | None = None,
        tenant_id: str | None = None,
        agent_id: str | None = None,
        channel: str | None = None,
        direction: str | None = None,
        started_at_ms: int | None = None,
        ended_at_ms: int | None = None,
        end_reason: str | None = None,
        is_probe: bool | int | None = None,
        reported_answer_latency_ms: int | None = None,
        reported_answer_latency_source: str | None = None,
    ) -> str:
        """Delegate to calls_repository.upsert_call; return the ``calls.id``.

        Creates the row if this is the first event seen for the call, so an
        out-of-order or redelivered webhook is never dropped.

        ``reported_answer_latency_*`` carry a caller-MEASURED answer latency,
        which today means only ``sipp_rtd``: the true INVITE->200 wall time a
        load worker saw, and the strongest source in the precedence. Without this
        passthrough the repository accepts it but no HTTP writer can reach it, so
        the top tier would be dead code and every number would degrade to a
        derived one. The repository still enforces the precedence and rejects any
        source a caller may not assert.
        """
        from voicegateway.repository import calls_repository

        await self._ensure_initialized()
        async with self._conn.session() as db:
            return await calls_repository.upsert_call(
                db,
                origin=origin,
                room_sid=room_sid,
                attempt_id=attempt_id,
                room_name=room_name,
                run_id=run_id,
                project=project,
                tenant_id=tenant_id,
                agent_id=agent_id,
                channel=channel,
                direction=direction,
                started_at_ms=started_at_ms,
                ended_at_ms=ended_at_ms,
                end_reason=end_reason,
                is_probe=is_probe,
                reported_answer_latency_ms=reported_answer_latency_ms,
                reported_answer_latency_source=reported_answer_latency_source,
            )

    async def upsert_call_leg(
        self,
        *,
        call_id: str,
        participant_sid: str,
        identity: str | None = None,
        kind: str | None = None,
        region: str | None = None,
        joined_at_ms: int | None = None,
        left_at_ms: int | None = None,
        disconnect_reason: str | None = None,
        is_publisher: bool | int | None = None,
        attributes: dict[str, Any] | None = None,
        first_audio_track_at_ms: int | None = None,
        audio_track_sid: str | None = None,
        audio_codec: str | None = None,
        source: str | None = None,
    ) -> None:
        """Delegate to calls_repository.upsert_call_leg (one row per participant).

        ``source`` is the origin of THIS event's writer ("agent"/"loadgen" for a
        self-report, "webhook" for LiveKit). It is what lets the answer-latency
        derivation tell a millisecond in-process clock from a webhook's
        whole-second ``created_at``; omitting it under-claims as webhook precision.
        """
        from voicegateway.repository import calls_repository

        await self._ensure_initialized()
        async with self._conn.session() as db:
            await calls_repository.upsert_call_leg(
                db,
                call_id=call_id,
                participant_sid=participant_sid,
                identity=identity,
                kind=kind,
                region=region,
                joined_at_ms=joined_at_ms,
                left_at_ms=left_at_ms,
                disconnect_reason=disconnect_reason,
                is_publisher=is_publisher,
                attributes=attributes,
                first_audio_track_at_ms=first_audio_track_at_ms,
                audio_track_sid=audio_track_sid,
                audio_codec=audio_codec,
                source=source,
            )

    async def get_call(self, call_id: str) -> dict[str, Any] | None:
        """One call by id, as a dict, or None."""
        import dataclasses

        from voicegateway.repository import calls_repository

        await self._ensure_initialized()
        async with self._conn.session() as db:
            row = await calls_repository.get_call(db, call_id)
        return None if row is None else dataclasses.asdict(row)

    async def get_call_by_room_sid(self, room_sid: str) -> dict[str, Any] | None:
        """One call by LiveKit room SID, as a dict, or None."""
        import dataclasses

        from voicegateway.repository import calls_repository

        await self._ensure_initialized()
        async with self._conn.session() as db:
            row = await calls_repository.get_call_by_room_sid(db, room_sid)
        return None if row is None else dataclasses.asdict(row)

    async def list_calls(
        self,
        limit: int = 100,
        project: str | None = None,
        run_id: str | None = None,
        tenant: str | None = None,
        is_probe: bool | None = False,
    ) -> list[dict[str, Any]]:
        """Newest calls first. ``is_probe=False`` (the default) excludes
        load-test traffic; ``True`` returns only it, ``None`` returns every row.

        ``tenant`` carries the same read-scope convention as the other list
        passthroughs on this service (``list_sessions``,
        ``get_recent_requests``): ``None`` is every tenant, ``""`` is the
        unattributed bucket (``tenant_id IS NULL``), anything else is that
        tenant. It is applied in SQL, so ``limit`` bounds the rows RETURNED for
        the scoped caller and not merely the rows scanned before a caller-side
        filter thins them.
        """
        import dataclasses

        from voicegateway.repository import calls_repository

        await self._ensure_initialized()
        async with self._conn.session() as db:
            rows = await calls_repository.list_calls(
                db,
                limit=limit,
                project=project,
                run_id=run_id,
                tenant=tenant,
                is_probe=is_probe,
            )
        return [dataclasses.asdict(r) for r in rows]

    async def list_call_legs(self, call_id: str) -> list[dict[str, Any]]:
        """One call's legs, earliest join first, as dicts."""
        import dataclasses

        from voicegateway.repository import calls_repository

        await self._ensure_initialized()
        async with self._conn.session() as db:
            rows = await calls_repository.list_call_legs(db, call_id)
        return [dataclasses.asdict(r) for r in rows]

    # ------------------------------------------------------------------
    # Node samples (layer 7 scrapes) against the calls they OVERLAP
    # ------------------------------------------------------------------

    async def correlate_recent_calls_to_nodes(
        self,
        *,
        limit: int = 5,
        pad_ms: int | None = None,
    ) -> dict[str, Any]:
        """Recent calls, each with the node samples that OVERLAP its window.

        Overlap, never attribution. ``node_samples`` has no ``call_id`` (layer 7
        counters are node-wide), so a node listed against a call means exactly
        "rows exist for that node inside this call's padded window" -- not that
        the call's media crossed it. Two concurrent calls correlate to the same
        nodes, and a call on a node nobody scrapes correlates to none.

        Nothing here computes. The window, its padding, the status and every
        summary come from ``node_correlation_repository.correlate_call_window``;
        this composes it over the recent calls (the repository takes a
        :class:`CallRow`, which is why the composition cannot live in the
        handler) and serialises the dataclasses.

        Shape::

            {"pad_ms": int,
             "samples_stored": int,
             "calls": [<calls row> + {"correlation": <WindowCorrelation>|null}]}

        ``pad_ms`` is echoed at the top level as well as on every window so the
        pad is readable even when there is no call to carry one: "within 15 s of
        this call" is a weaker claim than "during this call", and a reader who
        cannot see the pad cannot judge which one they are holding.

        ``samples_stored`` is the whole ``node_samples`` row count. It is what
        separates "no scrape target is configured in this deployment" from "this
        particular window had nothing in it", which
        ``WindowCorrelation.status == "no_samples"`` alone cannot say.

        ``correlation`` is ``null`` only when the CALL has no closed span (still
        in flight, or an INVITE that never produced a room). That is not
        ``no_samples``: nothing was looked for, because substituting "now" for a
        missing end would invent the span.

        Load-test traffic is excluded, the same ``is_probe=False`` default
        ``list_calls`` (and therefore ``/api/calls``) applies.
        """
        import dataclasses

        from voicegateway.repository import calls_repository
        from voicegateway.repository import node_correlation_repository as correlate
        from voicegateway.repository import node_samples_repository as node_samples

        resolved_pad = correlate.DEFAULT_WINDOW_PAD_MS if pad_ms is None else pad_ms
        await self._ensure_initialized()
        out: list[dict[str, Any]] = []
        async with self._conn.session() as db:
            stored = await node_samples.count_samples(db)
            rows = await calls_repository.list_calls(db, limit=limit)
            for row in rows:
                correlation = await correlate.correlate_call_window(
                    db, call=row, pad_ms=resolved_pad
                )
                out.append(
                    {
                        **dataclasses.asdict(row),
                        "correlation": (
                            None
                            if correlation is None
                            else dataclasses.asdict(correlation)
                        ),
                    }
                )
        return {"pad_ms": resolved_pad, "samples_stored": stored, "calls": out}

    # ------------------------------------------------------------------
    # Diagnostics runs (LiveKit probe runs started from the dashboard)
    # ------------------------------------------------------------------

    async def upsert_diagnostics_run(
        self,
        *,
        run_id: str,
        checks: list[str],
        config: dict[str, Any],
        status: str,
        results: dict[str, Any] | None = None,
        verdict: str | None = None,
        error: str | None = None,
        created_at: str,
        started_at: str | None = None,
        ended_at: str | None = None,
        project: str | None = None,
    ) -> None:
        """Delegate to diagnostics_runs_repository.upsert_run.

        Called once per state transition, so a run interrupted by a restart still
        left its last observed state on disk.
        """
        from voicegateway.repository import diagnostics_runs_repository as repo

        await self._ensure_initialized()
        async with self._conn.session() as db:
            await repo.upsert_run(
                db,
                run_id=run_id,
                checks=checks,
                config=config,
                status=status,
                results=results,
                verdict=verdict,
                error=error,
                created_at=created_at,
                started_at=started_at,
                ended_at=ended_at,
                project=project or repo.DEFAULT_PROJECT,
            )

    async def get_diagnostics_run(self, run_id: str) -> dict[str, Any] | None:
        """One diagnostics run by id, as a dict, or None."""
        import dataclasses

        from voicegateway.repository import diagnostics_runs_repository as repo

        await self._ensure_initialized()
        async with self._conn.session() as db:
            row = await repo.get_run(db, run_id)
        return None if row is None else dataclasses.asdict(row)

    async def list_diagnostics_runs(
        self, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Diagnostics runs, newest first, as dicts.

        ``limit`` bounds one response (a run carries its full probe payload); the
        stored history itself is bounded only by retention.
        """
        import dataclasses

        from voicegateway.repository import diagnostics_runs_repository as repo

        await self._ensure_initialized()
        async with self._conn.session() as db:
            rows = await repo.list_runs(
                db, limit=repo.DEFAULT_HISTORY_LIMIT if limit is None else limit
            )
        return [dataclasses.asdict(r) for r in rows]

    # ------------------------------------------------------------------
    # Managed providers / models / projects
    # ------------------------------------------------------------------

    # Managed providers
    async def list_managed_providers(self) -> list[dict[str, Any]]:
        """Delegate to ManagedConfigService.list_providers."""
        await self._ensure_initialized()
        return await self._managed_config_service.list_providers()

    async def get_managed_provider(self, provider_id: str) -> dict[str, Any] | None:
        """Delegate to ManagedConfigService.get_provider."""
        await self._ensure_initialized()
        return await self._managed_config_service.get_provider(provider_id)

    async def upsert_managed_provider(
        self,
        provider_id: str,
        provider_type: str,
        api_key: str,
        base_url: str | None = None,
        extra_config: dict[str, Any] | None = None,
        project: str | None = None,
    ) -> None:
        """Delegate to ManagedConfigService.upsert_provider."""
        await self._ensure_initialized()
        await self._managed_config_service.upsert_provider(
            provider_id,
            provider_type,
            api_key,
            base_url=base_url,
            extra_config=extra_config,
            project=project,
        )

    async def delete_managed_provider(self, provider_id: str) -> bool:
        """Delegate to ManagedConfigService.delete_provider."""
        await self._ensure_initialized()
        return await self._managed_config_service.delete_provider(provider_id)

    async def rotate_managed_credentials(
        self, *, time_now: float | None = None
    ) -> dict[str, Any]:
        """Delegate to ManagedConfigService.rotate_credentials."""
        await self._ensure_initialized()
        return await self._managed_config_service.rotate_credentials(time_now=time_now)

    # Managed models
    async def list_managed_models(self) -> list[dict[str, Any]]:
        """Delegate to ManagedConfigService.list_models."""
        await self._ensure_initialized()
        return await self._managed_config_service.list_models()

    async def get_managed_model(self, model_id: str) -> dict[str, Any] | None:
        """Delegate to ManagedConfigService.get_model."""
        await self._ensure_initialized()
        return await self._managed_config_service.get_model(model_id)

    async def upsert_managed_model(
        self,
        model_id: str,
        modality: str,
        provider_id: str,
        model_name: str,
        display_name: str | None = None,
        default_language: str | None = None,
        default_voice: str | None = None,
        extra_config: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> None:
        """Delegate to ManagedConfigService.upsert_model."""
        await self._ensure_initialized()
        await self._managed_config_service.upsert_model(
            model_id,
            modality,
            provider_id,
            model_name,
            display_name=display_name,
            default_language=default_language,
            default_voice=default_voice,
            extra_config=extra_config,
            enabled=enabled,
        )

    async def delete_managed_model(self, model_id: str) -> bool:
        """Delegate to ManagedConfigService.delete_model."""
        await self._ensure_initialized()
        return await self._managed_config_service.delete_model(model_id)

    # Managed projects
    async def list_managed_projects(self) -> list[dict[str, Any]]:
        """Delegate to ManagedConfigService.list_projects."""
        await self._ensure_initialized()
        return await self._managed_config_service.list_projects()

    async def get_managed_project(self, project_id: str) -> dict[str, Any] | None:
        """Delegate to ManagedConfigService.get_project."""
        await self._ensure_initialized()
        return await self._managed_config_service.get_project(project_id)

    async def upsert_managed_project(
        self,
        project_id: str,
        name: str,
        description: str = "",
        daily_budget: float = 0.0,
        budget_action: str = "warn",
        default_stack: str | None = None,
        stt_model: str | None = None,
        llm_model: str | None = None,
        tts_model: str | None = None,
        tags: list[str] | None = None,
        branding: dict[str, Any] | None = None,
    ) -> None:
        """Delegate to ManagedConfigService.upsert_project."""
        await self._ensure_initialized()
        await self._managed_config_service.upsert_project(
            project_id,
            name,
            description=description,
            daily_budget=daily_budget,
            budget_action=budget_action,
            default_stack=default_stack,
            stt_model=stt_model,
            llm_model=llm_model,
            tts_model=tts_model,
            tags=tags,
            branding=branding,
        )

    async def delete_managed_project(self, project_id: str) -> bool:
        """Delegate to ManagedConfigService.delete_project."""
        await self._ensure_initialized()
        return await self._managed_config_service.delete_project(project_id)

    # Rate rules (DB rate-card overrides)
    async def list_rate_rules(self) -> list[dict[str, Any]]:
        """Delegate to ManagedConfigService.list_rate_rules."""
        await self._ensure_initialized()
        return await self._managed_config_service.list_rate_rules()

    async def upsert_rate_rule(
        self,
        *,
        modality: str = "*",
        provider: str = "*",
        model: str = "*",
        tenant: str | None = None,
        plan: str | None = None,
        markup: float | None = None,
        fixed: float | None = None,
        unit: str | None = None,
    ) -> str:
        """Delegate to ManagedConfigService.upsert_rate_rule."""
        await self._ensure_initialized()
        return await self._managed_config_service.upsert_rate_rule(
            modality=modality,
            provider=provider,
            model=model,
            tenant=tenant,
            plan=plan,
            markup=markup,
            fixed=fixed,
            unit=unit,
        )

    async def delete_rate_rule(self, rule_id: str) -> bool:
        """Delegate to ManagedConfigService.delete_rate_rule."""
        await self._ensure_initialized()
        return await self._managed_config_service.delete_rate_rule(rule_id)
