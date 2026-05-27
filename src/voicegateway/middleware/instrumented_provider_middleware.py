"""Subclass-based instrumentation wrappers for LiveKit plugin instances."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from livekit.agents import llm as lk_llm
from livekit.agents import stt as lk_stt
from livekit.agents import tts as lk_tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN

from voicegateway.middleware.base_middleware import InstrumentationMixin

if TYPE_CHECKING:
    from voicegateway.middleware.cost_tracker_middleware import CostTracker
    from voicegateway.services.storage_service import StorageService

logger = logging.getLogger(__name__)

# Backwards-compat alias for tests still referencing the pre-promotion name.
_InstrumentedBase = InstrumentationMixin


class InstrumentedSTT(lk_stt.STT, InstrumentationMixin):
    """STT wrapper that records latency/cost via LK's metrics events."""

    _modality = "stt"

    def __init__(
        self,
        *,
        wrapped: lk_stt.STT,
        model_id: str,
        provider: str,
        project: str,
        cost_tracker: CostTracker,
        storage: StorageService | None,
    ) -> None:
        super().__init__(capabilities=wrapped.capabilities)
        self._init_instrumentation(
            wrapped=wrapped,
            model_id=model_id,
            provider=provider,
            project=project,
            cost_tracker=cost_tracker,
            storage=storage,
        )

    @property
    def label(self) -> str:
        # ``self._wrapped`` is typed as ``Any`` so its attribute access
        # returns ``Any``; the LK base classes guarantee the underlying
        # value is a string, but we coerce for mypy and to be defensive
        # against an exotic plugin returning a non-string label.
        return str(self._wrapped.label)

    @property
    def model(self) -> str:
        return str(self._wrapped.model)

    @property
    def provider(self) -> str:
        return str(self._wrapped.provider)

    async def _recognize_impl(
        self,
        buffer: Any,
        *,
        language: Any = NOT_GIVEN,
        conn_options: Any = DEFAULT_API_CONNECT_OPTIONS,
    ) -> Any:
        return await self._wrapped._recognize_impl(
            buffer, language=language, conn_options=conn_options
        )

    async def recognize(
        self,
        buffer: Any,
        *,
        language: Any = NOT_GIVEN,
        conn_options: Any = DEFAULT_API_CONNECT_OPTIONS,
    ) -> Any:
        return await self._wrapped.recognize(
            buffer, language=language, conn_options=conn_options
        )

    def stream(
        self,
        *,
        language: Any = NOT_GIVEN,
        conn_options: Any = DEFAULT_API_CONNECT_OPTIONS,
    ) -> Any:
        return self._wrapped.stream(language=language, conn_options=conn_options)

    def prewarm(self) -> None:
        self._wrapped.prewarm()

    async def aclose(self) -> None:
        await self._wrapped.aclose()
        # The wrapped.aclose path is where LK's stream terminal blocks
        # run, which is where metrics_collected fires; await any log tasks
        # the bridge scheduled so a graceful shutdown does not race the
        # process exit cancelling pending writes.
        await self._drain_pending_logs()

    def __repr__(self) -> str:
        return f"<InstrumentedSTT wrapping {self._wrapped!r}>"


class InstrumentedLLM(lk_llm.LLM, InstrumentationMixin):
    """LLM wrapper that records latency/cost via LK's metrics events."""

    _modality = "llm"

    def __init__(
        self,
        *,
        wrapped: lk_llm.LLM,
        model_id: str,
        provider: str,
        project: str,
        cost_tracker: CostTracker,
        storage: StorageService | None,
    ) -> None:
        super().__init__()
        self._init_instrumentation(
            wrapped=wrapped,
            model_id=model_id,
            provider=provider,
            project=project,
            cost_tracker=cost_tracker,
            storage=storage,
        )

    @property
    def label(self) -> str:
        # ``self._wrapped`` is typed as ``Any`` so its attribute access
        # returns ``Any``; the LK base classes guarantee the underlying
        # value is a string, but we coerce for mypy and to be defensive
        # against an exotic plugin returning a non-string label.
        return str(self._wrapped.label)

    @property
    def model(self) -> str:
        return str(self._wrapped.model)

    @property
    def provider(self) -> str:
        return str(self._wrapped.provider)

    def chat(
        self,
        *,
        chat_ctx: Any,
        tools: Any = None,
        conn_options: Any = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: Any = NOT_GIVEN,
        tool_choice: Any = NOT_GIVEN,
        extra_kwargs: Any = NOT_GIVEN,
    ) -> Any:
        chat_ctx, tools = self._apply_guardrails(chat_ctx=chat_ctx, tools=tools)
        return self._wrapped.chat(
            chat_ctx=chat_ctx,
            tools=tools,
            conn_options=conn_options,
            parallel_tool_calls=parallel_tool_calls,
            tool_choice=tool_choice,
            extra_kwargs=extra_kwargs,
        )

    def _apply_guardrails(self, *, chat_ctx: Any, tools: Any) -> tuple[Any, Any]:
        """Inject the guardrail prompt/tool when the project is active."""
        # Lazy: middleware <-> inference and middleware <-> guardrail-service
        # cycles. Hoisting these would force a deeper structural refactor;
        # see docs/superpowers/specs/2026-05-14-server-split-and-import-hoist-design.md.
        from voicegateway.core.gateway_factory import get_gateway
        from voicegateway.inference.session.context import (
            current_guardrails_bypassed,
            current_tenant,
            get_or_create_session_id,
            get_or_freeze_guardrail_policy_snapshot,
            guardrail_bypass_already_logged,
            mark_guardrail_bypass_logged,
        )
        from voicegateway.schemas.guardrail_policy_schema import (
            REPORT_GUARDRAIL_TOOL_NAME,
            GuardrailPolicy,
        )
        from voicegateway.services.guardrail_runtime_service import (
            create_report_guardrail_action_tool,
            inject_guardrail_block,
            schedule_bypass_event,
            tools_contain_reserved_report_tool,
        )
        from voicegateway.services.guardrail_service import compose_block

        try:
            gateway = get_gateway()
            project_cfg = gateway.config.projects.get(self.project)
        except Exception:
            project_cfg = None

        configured_policy = (
            project_cfg.guardrails
            if project_cfg is not None
            else GuardrailPolicy.disabled()
        )
        snapshot = get_or_freeze_guardrail_policy_snapshot(
            configured_policy.to_storage_dict()
        )
        policy = GuardrailPolicy.from_raw(snapshot)
        if not policy.is_active:
            return chat_ctx, tools

        sid = get_or_create_session_id()
        tenant_id = current_tenant()

        if current_guardrails_bypassed():
            if not guardrail_bypass_already_logged():
                schedule_bypass_event(
                    storage=self._storage,
                    session_id=sid,
                    tenant_id=tenant_id,
                )
                mark_guardrail_bypass_logged()
            return chat_ctx, tools

        tool_list = [] if tools is None or tools is NOT_GIVEN else list(tools)
        if tools_contain_reserved_report_tool(tool_list):
            raise ValueError(
                f"{REPORT_GUARDRAIL_TOOL_NAME!r} is reserved by VoiceGateway "
                "when project guardrails are active"
            )

        block = compose_block(policy)
        guarded_ctx = inject_guardrail_block(chat_ctx, block)
        report_tool = create_report_guardrail_action_tool(
            storage=self._storage,
            session_id=sid,
            tenant_id=tenant_id,
        )
        return guarded_ctx, [*tool_list, report_tool]

    def prewarm(self) -> None:
        self._wrapped.prewarm()

    async def aclose(self) -> None:
        await self._wrapped.aclose()
        # The wrapped.aclose path is where LK's stream terminal blocks
        # run, which is where metrics_collected fires; await any log tasks
        # the bridge scheduled so a graceful shutdown does not race the
        # process exit cancelling pending writes.
        await self._drain_pending_logs()

    def __repr__(self) -> str:
        return f"<InstrumentedLLM wrapping {self._wrapped!r}>"


class InstrumentedTTS(lk_tts.TTS, InstrumentationMixin):
    """TTS wrapper that records latency/cost via LK's metrics events."""

    _modality = "tts"

    def __init__(
        self,
        *,
        wrapped: lk_tts.TTS,
        model_id: str,
        provider: str,
        project: str,
        cost_tracker: CostTracker,
        storage: StorageService | None,
    ) -> None:
        super().__init__(
            capabilities=wrapped.capabilities,
            sample_rate=wrapped.sample_rate,
            num_channels=wrapped.num_channels,
        )
        self._init_instrumentation(
            wrapped=wrapped,
            model_id=model_id,
            provider=provider,
            project=project,
            cost_tracker=cost_tracker,
            storage=storage,
        )

    @property
    def label(self) -> str:
        # ``self._wrapped`` is typed as ``Any`` so its attribute access
        # returns ``Any``; the LK base classes guarantee the underlying
        # value is a string, but we coerce for mypy and to be defensive
        # against an exotic plugin returning a non-string label.
        return str(self._wrapped.label)

    @property
    def model(self) -> str:
        return str(self._wrapped.model)

    @property
    def provider(self) -> str:
        return str(self._wrapped.provider)

    def synthesize(
        self, text: str, *, conn_options: Any = DEFAULT_API_CONNECT_OPTIONS
    ) -> Any:
        return self._wrapped.synthesize(text, conn_options=conn_options)

    def stream(self, *, conn_options: Any = DEFAULT_API_CONNECT_OPTIONS) -> Any:
        return self._wrapped.stream(conn_options=conn_options)

    def prewarm(self) -> None:
        self._wrapped.prewarm()

    async def aclose(self) -> None:
        await self._wrapped.aclose()
        # The wrapped.aclose path is where LK's stream terminal blocks
        # run, which is where metrics_collected fires; await any log tasks
        # the bridge scheduled so a graceful shutdown does not race the
        # process exit cancelling pending writes.
        await self._drain_pending_logs()

    def __repr__(self) -> str:
        return f"<InstrumentedTTS wrapping {self._wrapped!r}>"


def wrap_provider(
    instance: Any,
    modality: str,
    model_id: str,
    provider: str,
    project: str,
    cost_tracker: CostTracker,
    storage: StorageService | None,
) -> Any:
    """Wrap a provider instance with instrumentation."""
    wrapper_cls = {
        "stt": InstrumentedSTT,
        "llm": InstrumentedLLM,
        "tts": InstrumentedTTS,
    }.get(modality)

    if wrapper_cls is None:
        return instance

    return wrapper_cls(
        wrapped=instance,
        model_id=model_id,
        provider=provider,
        project=project,
        cost_tracker=cost_tracker,
        storage=storage,
    )
