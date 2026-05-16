"""End-to-end integration test for v0.0.5 session correlation."""

from __future__ import annotations

import contextvars
import sqlite3
from typing import Any

import pytest
import yaml

from voicegateway.core import gateway_factory as factory
from voicegateway.inference import llm_inference as llm
from voicegateway.inference import stt_inference as stt
from voicegateway.inference import tts_inference as tts
from voicegateway.inference.session.context import get_session_id

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeInstance:
    """Generic stand-in for livekit.plugins.<provider>.STT/LLM/TTS."""

    def __init__(self, *, model: str = "test", **kwargs: Any) -> None:
        from livekit.agents.stt import STTCapabilities
        from livekit.agents.tts import TTSCapabilities

        # Both STT and TTS capabilities — the wrapper picks whichever
        # the LK base class for that modality reads.
        self.capabilities: Any = STTCapabilities(streaming=False, interim_results=False)
        self._tts_capabilities: Any = TTSCapabilities(streaming=False)
        self.sample_rate = 24000
        self.num_channels = 1
        self.model = model
        self.kwargs = kwargs

    def on(self, event: str, callback: Any) -> None:
        # No-op; the wrapper bridges events but the fake never fires.
        pass


class _FakeSTTInstance(_FakeInstance):
    pass


class _FakeLLMInstance(_FakeInstance):
    pass


class _FakeTTSInstance(_FakeInstance):
    def __init__(self, *, model: str = "test", **kwargs: Any) -> None:
        super().__init__(model=model, **kwargs)
        # TTS-specific: the wrapper reads .capabilities expecting a
        # TTSCapabilities, so swap it in for this modality.
        self.capabilities = self._tts_capabilities


class _FakeProvider:
    """A BaseProvider-like fake that returns _FakeInstance on every modality."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    def create_stt(self, model: str, **kwargs: Any) -> _FakeInstance:
        return _FakeSTTInstance(model=model, **kwargs)

    def create_llm(self, model: str, **kwargs: Any) -> _FakeInstance:
        return _FakeLLMInstance(model=model, **kwargs)

    def create_tts(
        self, model: str, voice: str | None = None, **kwargs: Any
    ) -> _FakeInstance:
        return _FakeTTSInstance(model=model, voice=voice, **kwargs)

    async def health_check(self) -> bool:
        return True


@pytest.fixture
def fake_providers(monkeypatch):
    """Replace create_provider in all three inference modules so the"""

    def _create(_provider_name: str, config: dict[str, Any]) -> _FakeProvider:
        return _FakeProvider(config)

    monkeypatch.setattr("voicegateway.core.registry.create_provider", _create)


@pytest.fixture
def configured_gateway(tmp_path, monkeypatch):
    cfg = {
        "providers": {
            "deepgram": {"api_key": "dg-test"},
            "openai": {"api_key": "oa-test"},
            "cartesia": {"api_key": "ca-test"},
        },
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "stacks": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": True},
        "observability": {"latency_tracking": True},
    }
    config_path = tmp_path / "voicegw.yaml"
    config_path.write_text(yaml.dump(cfg))

    db_path = str(tmp_path / "session-correlation.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)

    from voicegateway.core.gateway import Gateway

    gw = Gateway(config_path=str(config_path))
    monkeypatch.setattr(factory, "_gateway", gw)
    return gw, db_path


# ---------------------------------------------------------------------------
# The integration test
# ---------------------------------------------------------------------------


async def _run_in_isolated_context(coro_factory):
    """Run a coroutine inside an isolated copied contextvars.Context."""
    import asyncio

    ctx = contextvars.copy_context()
    task = asyncio.create_task(coro_factory(), context=ctx)
    return await task


async def test_three_modalities_share_one_session_and_one_sessions_row(
    configured_gateway, fake_providers
):
    gateway, db_path = configured_gateway

    captured: dict[str, Any] = {}

    async def _scenario():
        # All three factories run inside this single context, so the
        # FIRST one creates the session ID via get_or_create_session_id
        # and the OTHER two inherit it via the ContextVar lookup.
        stt_inst = stt.STT("deepgram/nova-3")
        llm_inst = llm.LLM("openai/gpt-4o-mini")
        tts_inst = tts.TTS("cartesia/sonic-3")

        captured["sid_at_construction"] = get_session_id()

        # Drive each wrapper's logging path with a non-zero cost so the
        # sessions row's total_cost_usd is non-trivial. The exact
        # billing semantics (genai-prices etc.) aren't the point of
        # this test; the mock model_id won't be in the catalog and
        # cost_usd resolves to 0. We bump input_units so cost-by-source
        # tracking still records a row.
        await stt_inst._log_request(input_units=1.0)
        await llm_inst._log_request(input_units=100.0, output_units=50.0)
        await tts_inst._log_request(input_units=200.0)

    await _run_in_isolated_context(_scenario)

    sid = captured["sid_at_construction"]
    assert sid is not None
    assert sid.startswith("vg-"), f"expected vg-<uuid>, got {sid!r}"

    # ---- 1. requests table: three rows, all sharing the same sid.
    requests = await gateway.storage.get_recent_requests(limit=10)
    assert len(requests) == 3
    sids = {r["session_id"] for r in requests}
    assert sids == {sid}
    modalities = sorted(r["modality"] for r in requests)
    assert modalities == ["llm", "stt", "tts"]

    # ---- 2. sessions table: one row, request_count=3, all 3 modalities.
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT id, modalities, request_count FROM sessions")
        rows = cursor.fetchall()
    finally:
        conn.close()

    assert len(rows) == 1, (
        f"expected exactly one sessions row, got {len(rows)} — "
        "this would mean the three modalities created different IDs"
    )
    row_id, mods_str, count = rows[0]
    assert row_id == sid
    assert count == 3
    assert sorted(mods_str.split(",")) == ["llm", "stt", "tts"]


async def test_two_async_contexts_get_separate_sessions(
    configured_gateway, fake_providers
):
    """The negative half of the contract. Two independently-copied"""
    gateway, db_path = configured_gateway

    sids: list[str] = []

    async def _ctx_a():
        stt_inst = stt.STT("deepgram/nova-3")
        sids.append(get_session_id() or "")
        await stt_inst._log_request(input_units=1.0)

    async def _ctx_b():
        llm_inst = llm.LLM("openai/gpt-4o-mini")
        sids.append(get_session_id() or "")
        await llm_inst._log_request(input_units=100.0)

    await _run_in_isolated_context(_ctx_a)
    await _run_in_isolated_context(_ctx_b)

    assert sids[0] != sids[1]
    assert all(s.startswith("vg-") for s in sids)

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM sessions")
        count = cursor.fetchone()[0]
    finally:
        conn.close()
    # One sessions row per independent context.
    assert count == 2


async def test_n_requests_within_one_context_collapse_to_one_sessions_row(
    configured_gateway, fake_providers
):
    """Five separate STT factory calls inside ONE context produce five"""
    _, db_path = configured_gateway

    async def _scenario():
        for _ in range(5):
            stt_inst = stt.STT("deepgram/nova-3")
            await stt_inst._log_request(input_units=1.0)

    await _run_in_isolated_context(_scenario)

    conn = sqlite3.connect(db_path)
    try:
        sess_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        req_count = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        sess_request_count = conn.execute(
            "SELECT request_count FROM sessions"
        ).fetchone()[0]
    finally:
        conn.close()
    assert sess_count == 1, (
        "five requests in one session must collapse into exactly one "
        "sessions row; >1 means the UPSERT keyed off the wrong column"
    )
    assert req_count == 5
    assert sess_request_count == 5
