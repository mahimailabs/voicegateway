"""Tests for v0.6.0 guardrail prompt/tool injection."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from livekit.agents import llm as lk_llm

from voicegateway.core import gateway_factory as factory
from voicegateway.inference.session.context import reset_session_id, start_session
from voicegateway.middleware.instrumented_provider_middleware import InstrumentedLLM
from voicegateway.repository import guardrail_events_repository as guardrail_events
from voicegateway.schemas.guardrail_policy_schema import (
    REPORT_GUARDRAIL_TOOL_NAME,
    GuardrailPolicy,
)
from voicegateway.services.guardrail_runtime_service import (
    create_report_guardrail_action_tool,
    inject_guardrail_block,
    tools_contain_reserved_report_tool,
)
from voicegateway.services.guardrail_service import compose_block, load_prompt
from voicegateway.services.storage_service import StorageService


@pytest.fixture(autouse=True)
def _isolate_session_context():
    reset_session_id()
    yield
    reset_session_id()


class _WrappedLLM:
    label = "fake"
    model = "fake-model"
    provider = "fake-provider"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def on(self, event: str, callback) -> None:
        pass

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return "chat-stream"


def _active_policy(**categories: str) -> GuardrailPolicy:
    return GuardrailPolicy.from_raw({"enabled": True, "categories": categories})


def _wrapper(policy: GuardrailPolicy, monkeypatch, storage=None) -> InstrumentedLLM:
    gw = SimpleNamespace(
        config=SimpleNamespace(projects={"default": SimpleNamespace(guardrails=policy)})
    )
    monkeypatch.setattr(factory, "_gateway", gw)
    return InstrumentedLLM(
        wrapped=_WrappedLLM(),
        model_id="openai/gpt-test",
        provider="openai",
        project="default",
        cost_tracker=MagicMock(),
        storage=storage,
    )


async def _wait_for_guardrail_events(storage: StorageService, sid: str):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    events = []
    while loop.time() < deadline:
        await storage._ensure_initialized()
        async with storage._conn.session() as db:
            events = await guardrail_events.list_events_by_session(db, sid)
        if events:
            return events
        await asyncio.sleep(0.01)
    return events


def test_prompt_assets_and_composer_include_versioned_tool_contract() -> None:
    assert "identifiers" in load_prompt("pii").lower()

    block = compose_block(_active_policy(pii="redact"))

    assert '<voicegateway_guardrails version="v0.6.0">' in block
    assert "## pii" in block
    assert "Action: redact" in block
    assert REPORT_GUARDRAIL_TOOL_NAME in block


def test_composer_returns_empty_for_inactive_policy() -> None:
    assert compose_block(GuardrailPolicy.disabled()) == ""


def test_inject_guardrail_block_appends_after_system_and_developer() -> None:
    ctx = lk_llm.ChatContext(
        [
            lk_llm.ChatMessage(role="system", content=["system one"]),
            lk_llm.ChatMessage(role="developer", content=["developer one"]),
            lk_llm.ChatMessage(role="user", content=["hello"]),
        ]
    )

    injected = inject_guardrail_block(
        ctx, "<voicegateway_guardrails>rules</voicegateway_guardrails>"
    )

    assert injected is not ctx
    assert [item.role for item in injected.items] == [
        "system",
        "developer",
        "system",
        "user",
    ]
    assert "rules" in injected.items[2].text_content
    assert (
        len(
            inject_guardrail_block(
                injected, "<voicegateway_guardrails>rules</voicegateway_guardrails>"
            ).items
        )
        == 4
    )


def test_reserved_tool_detection_accepts_livekit_and_dict_shapes() -> None:
    tool = create_report_guardrail_action_tool(
        storage=None,
        session_id="vg-test",
        tenant_id=None,
    )

    assert tools_contain_reserved_report_tool([tool]) is True
    assert tools_contain_reserved_report_tool([{"name": REPORT_GUARDRAIL_TOOL_NAME}])
    assert tools_contain_reserved_report_tool([{"id": REPORT_GUARDRAIL_TOOL_NAME}])
    assert tools_contain_reserved_report_tool([{"name": "other"}]) is False


def test_instrumented_llm_injects_prompt_and_report_tool(monkeypatch) -> None:
    reset_session_id()
    wrapper = _wrapper(_active_policy(pii="redact"), monkeypatch)
    chat_ctx = lk_llm.ChatContext(
        [lk_llm.ChatMessage(role="user", content=["my ssn is 123"])]
    )

    result = wrapper.chat(chat_ctx=chat_ctx, tools=[])

    assert result == "chat-stream"
    call = wrapper._wrapped.calls[0]
    assert call["chat_ctx"] is not chat_ctx
    assert REPORT_GUARDRAIL_TOOL_NAME in call["chat_ctx"].items[0].text_content
    assert [getattr(tool, "id", None) for tool in call["tools"]] == [
        REPORT_GUARDRAIL_TOOL_NAME
    ]


def test_instrumented_llm_freezes_policy_for_session(monkeypatch) -> None:
    reset_session_id()
    initial = _active_policy(pii="redact")
    wrapper = _wrapper(initial, monkeypatch)

    wrapper.chat(chat_ctx=lk_llm.ChatContext(), tools=[])
    wrapper._wrapped.calls.clear()
    factory._gateway.config.projects["default"].guardrails = _active_policy(
        financial="block"
    )

    wrapper.chat(chat_ctx=lk_llm.ChatContext(), tools=[])
    block = wrapper._wrapped.calls[0]["chat_ctx"].items[0].text_content

    assert "## pii" in block
    assert "## financial" not in block


def test_instrumented_llm_rejects_reserved_user_tool(monkeypatch) -> None:
    reset_session_id()
    wrapper = _wrapper(_active_policy(prompt_injection="block"), monkeypatch)

    try:
        wrapper.chat(
            chat_ctx=lk_llm.ChatContext(),
            tools=[{"name": REPORT_GUARDRAIL_TOOL_NAME}],
        )
    except ValueError as exc:
        assert "reserved by VoiceGateway" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("reserved tool name was accepted")


async def test_bypass_skips_injection_and_records_audit(tmp_path, monkeypatch) -> None:
    storage = StorageService(str(tmp_path / "guardrail-bypass.db"))
    sid = start_session(bypass_guardrails=True)
    wrapper = _wrapper(_active_policy(medical="alert"), monkeypatch, storage=storage)

    wrapper.chat(chat_ctx=lk_llm.ChatContext(), tools=None)

    call = wrapper._wrapped.calls[0]
    assert call["tools"] is None
    assert call["chat_ctx"].items == []

    events = await _wait_for_guardrail_events(storage, sid)
    assert len(events) == 1
    assert events[0].event_type == "bypassed"
    assert events[0].category is None
    assert events[0].action is None
