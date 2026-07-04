"""Worker presence: registration, idle/busy tracking, and the heartbeat push."""

from __future__ import annotations

import pytest

from voicegateway.fleet import worker


@pytest.fixture(autouse=True)
def _reset():
    worker._worker = None
    worker._pusher = None
    worker._collector_url = None
    worker._api_key = None
    worker._client = None
    yield
    worker._worker = None
    worker._pusher = None
    worker._client = None


def test_register_populates_presence(monkeypatch):
    monkeypatch.setenv("VOICEGW_AGENT_ID", "w1")
    aid = worker.register_worker(
        "realty", project="p", tenant_id="t", collector_url="http://c", api_key="k"
    )
    assert aid == "w1"
    p = worker._worker.presence()
    assert p["agent_id"] == "w1"
    assert p["agent_name"] == "realty"
    assert p["status"] == "idle"
    assert p["active_sessions"] == 0
    assert p["project"] == "p"
    assert p["tenant_id"] == "t"
    assert p["version"]  # from _version


def test_bump_active_toggles_status_and_clamps(monkeypatch):
    monkeypatch.setenv("VOICEGW_AGENT_ID", "w1")
    worker.register_worker("realty")
    worker.bump_active(1)
    assert worker._worker.status == "busy"
    assert worker._worker.active_sessions == 1
    worker.bump_active(1)
    assert worker._worker.active_sessions == 2
    worker.bump_active(-1)
    worker.bump_active(-1)
    assert worker._worker.status == "idle"
    worker.bump_active(-1)  # never goes negative
    assert worker._worker.active_sessions == 0


def test_bump_active_without_worker_is_noop():
    worker.bump_active(1)
    assert worker._worker is None


async def test_push_once_posts_presence_with_bearer(monkeypatch):
    monkeypatch.setenv("VOICEGW_AGENT_ID", "w1")
    monkeypatch.delenv("VOICEGW_COLLECTOR_URL", raising=False)
    worker.register_worker("realty")  # no collector -> no auto-pusher started
    worker._collector_url = "http://collector"
    worker._api_key = "vk_x"
    posts: list = []

    class _FakeClient:
        async def post(self, url, json=None, headers=None):
            posts.append((url, json, headers))

    worker._client = _FakeClient()
    await worker.push_once()
    assert len(posts) == 1
    url, body, headers = posts[0]
    assert url == "http://collector/v1/agents/heartbeat"
    assert body["agent_name"] == "realty"
    assert body["status"] == "idle"
    assert headers["Authorization"] == "Bearer vk_x"


async def test_push_once_noop_without_collector(monkeypatch):
    monkeypatch.setenv("VOICEGW_AGENT_ID", "w1")
    monkeypatch.delenv("VOICEGW_COLLECTOR_URL", raising=False)
    worker.register_worker("realty")
    posts: list = []

    class _FakeClient:
        async def post(self, *a, **k):
            posts.append(1)

    worker._client = _FakeClient()
    await worker.push_once()
    assert posts == []
