"""Tests for the OpenRTC SessionObserver adapter (VoiceGatewayObserver)."""

from __future__ import annotations

import pickle
from typing import Any

from voicegateway.openrtc import VoiceGatewayObserver


class _FakeInfo:
    """Stand-in for openrtc.SessionInfo (duck-typed: agent_name + metadata)."""

    def __init__(self, agent_name: str, metadata: dict[str, str]) -> None:
        self.agent_name = agent_name
        self.metadata = metadata


class _FakeOutcome:
    """Stand-in for openrtc.SessionOutcome."""


class _FakeSession:
    """Stand-in for livekit.agents.AgentSession."""


class _SentinelSink:
    """A sink stand-in that records flush calls."""

    def __init__(self) -> None:
        self.flushes = 0
        self.closed = False

    async def flush(self) -> None:
        self.flushes += 1

    async def aclose(self) -> None:
        self.closed = True


def _patch(monkeypatch: Any) -> tuple[list[dict[str, Any]], _SentinelSink, list[Any]]:
    """Patch attach (recorder) and _build_default_sink (sentinel). Returns
    (attach_calls, the_one_sink, build_args)."""
    attach_calls: list[dict[str, Any]] = []
    the_sink = _SentinelSink()
    build_args: list[Any] = []

    def _fake_attach(session: Any, **kwargs: Any) -> str:
        attach_calls.append({"session": session, **kwargs})
        return "session-id"

    def _fake_build(collector_url: Any, api_key: Any, db_path: Any = None) -> Any:
        build_args.append((collector_url, api_key, db_path))
        return the_sink

    monkeypatch.setattr("voicegateway.openrtc.attach", _fake_attach)
    monkeypatch.setattr("voicegateway.openrtc._build_default_sink", _fake_build)
    return attach_calls, the_sink, build_args


async def test_on_session_start_calls_attach_with_mapped_attribution(
    monkeypatch: Any,
) -> None:
    attach_calls, the_sink, _ = _patch(monkeypatch)
    obs = VoiceGatewayObserver(project="prod")
    info = _FakeInfo("restaurant", {"tenant": "acme"})

    await obs.on_session_start(info, _FakeSession())

    assert len(attach_calls) == 1
    call = attach_calls[0]
    assert call["project"] == "prod"
    assert call["agent_id"] == "restaurant"
    assert call["tenant_id"] == "acme"
    assert call["sink"] is the_sink


async def test_tenant_id_is_none_when_metadata_lacks_tenant(
    monkeypatch: Any,
) -> None:
    attach_calls, _, _ = _patch(monkeypatch)
    obs = VoiceGatewayObserver()
    await obs.on_session_start(_FakeInfo("dental", {}), _FakeSession())
    assert attach_calls[0]["tenant_id"] is None


async def test_sink_is_built_once_and_shared_across_sessions(
    monkeypatch: Any,
) -> None:
    attach_calls, the_sink, build_args = _patch(monkeypatch)
    obs = VoiceGatewayObserver(collector_url="https://c.example.com")

    for _ in range(3):
        await obs.on_session_start(_FakeInfo("a", {}), _FakeSession())

    assert len(build_args) == 1  # built exactly once
    assert all(call["sink"] is the_sink for call in attach_calls)


async def test_env_fallbacks_and_constructor_precedence(
    monkeypatch: Any,
) -> None:
    _, _, build_args = _patch(monkeypatch)
    monkeypatch.setenv("VOICEGW_COLLECTOR_URL", "https://env-collector")
    monkeypatch.setenv("VOICEGW_API_KEY", "vk_env")

    # No constructor collector/key: env wins.
    obs_env = VoiceGatewayObserver()
    await obs_env.on_session_start(_FakeInfo("a", {}), _FakeSession())
    assert build_args[-1][0] == "https://env-collector"
    assert build_args[-1][1] == "vk_env"

    # Constructor beats env.
    obs_explicit = VoiceGatewayObserver(
        collector_url="https://explicit", api_key="vk_explicit"
    )
    await obs_explicit.on_session_start(_FakeInfo("a", {}), _FakeSession())
    assert build_args[-1][0] == "https://explicit"
    assert build_args[-1][1] == "vk_explicit"


async def test_db_path_passed_through_to_build(monkeypatch: Any) -> None:
    _, _, build_args = _patch(monkeypatch)
    obs = VoiceGatewayObserver(db_path="/tmp/vg.db")
    await obs.on_session_start(_FakeInfo("a", {}), _FakeSession())
    assert build_args[-1][2] == "/tmp/vg.db"


async def test_on_session_end_flushes(monkeypatch: Any) -> None:
    _, the_sink, _ = _patch(monkeypatch)
    obs = VoiceGatewayObserver()
    await obs.on_session_start(_FakeInfo("a", {}), _FakeSession())

    await obs.on_session_end(_FakeInfo("a", {}), _FakeOutcome())

    assert the_sink.flushes >= 1
    assert the_sink.closed is False  # flush, never aclose


async def test_on_session_end_without_start_does_not_raise(
    monkeypatch: Any,
) -> None:
    _, _, build_args = _patch(monkeypatch)
    obs = VoiceGatewayObserver()
    await obs.on_session_end(_FakeInfo("a", {}), _FakeOutcome())  # no start first
    assert build_args == []  # no sink built


def test_observer_is_picklable_and_drops_live_sink(monkeypatch: Any) -> None:
    _patch(monkeypatch)
    obs = VoiceGatewayObserver(project="prod", collector_url="https://c")
    obs._ensure_sink()  # build a live sink
    assert obs._sink is not None

    restored = pickle.loads(pickle.dumps(obs))

    assert isinstance(restored, VoiceGatewayObserver)
    assert restored._project == "prod"
    assert restored._collector_url == "https://c"
    assert restored._sink is None  # spawn contract: live sink dropped
