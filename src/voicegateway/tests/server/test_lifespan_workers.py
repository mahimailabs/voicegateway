"""Phase 3, Step 8: the lifespan starts and stops the background workers."""

from __future__ import annotations

import pytest
import yaml
from starlette.testclient import TestClient

from voicegateway.core import events
from voicegateway.core.events import lifespan
from voicegateway.core.gateway import Gateway
from voicegateway.server import build_app


def _gateway(tmp_path, monkeypatch, config: dict, *, db: bool = True) -> Gateway:
    if db:
        monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "ls.db"))
    else:
        monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    path = tmp_path / "voicegw.yaml"
    path.write_text(yaml.dump(config))
    return Gateway(config_path=str(path))


async def test_starts_three_workers_when_enabled(tmp_path, monkeypatch) -> None:
    gw = _gateway(tmp_path, monkeypatch, {"cost_tracking": {"enabled": True}})
    app = build_app(gw)
    async with lifespan(app):
        workers = app.state.workers
        assert len(workers) == 3
        assert all(w._task is not None for w in workers)
    assert all(w._task is None for w in workers)  # stopped on shutdown


async def test_starts_none_when_storage_disabled(tmp_path, monkeypatch) -> None:
    gw = _gateway(
        tmp_path, monkeypatch, {"cost_tracking": {"enabled": False}}, db=False
    )
    app = build_app(gw)
    async with lifespan(app):
        assert app.state.workers == []


async def test_starts_none_when_workers_disabled(tmp_path, monkeypatch) -> None:
    gw = _gateway(
        tmp_path,
        monkeypatch,
        {"cost_tracking": {"enabled": True}, "workers": {"enabled": False}},
    )
    app = build_app(gw)
    async with lifespan(app):
        assert app.state.workers == []


async def test_configured_intervals_are_applied(tmp_path, monkeypatch) -> None:
    gw = _gateway(
        tmp_path,
        monkeypatch,
        {
            "cost_tracking": {"enabled": True},
            "workers": {
                "rollup_interval_seconds": 111,
                "retention_interval_seconds": 222,
            },
        },
    )
    app = build_app(gw)
    async with lifespan(app):
        intervals = sorted(w._poll_interval for w in app.state.workers)
    # Two rollup workers at 111, the retention worker at 222.
    assert intervals == [111, 111, 222]


def test_lifespan_is_attached_to_the_app(tmp_path, monkeypatch) -> None:
    # TestClient drives the ASGI lifespan, proving _make_app wired it.
    gw = _gateway(tmp_path, monkeypatch, {"cost_tracking": {"enabled": True}})
    app = build_app(gw)
    with TestClient(app):
        assert len(app.state.workers) == 3


class _FakeWorker:
    def __init__(self, *, fail: bool = False) -> None:
        self.started = False
        self.stopped = False
        self._fail = fail

    async def start(self) -> None:
        if self._fail:
            raise RuntimeError("boom")
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


async def test_partial_start_failure_stops_started_workers(
    tmp_path, monkeypatch
) -> None:
    good = _FakeWorker()
    bad = _FakeWorker(fail=True)
    monkeypatch.setattr(events, "_build_workers", lambda gateway: [good, bad])
    gw = _gateway(tmp_path, monkeypatch, {"cost_tracking": {"enabled": True}})
    app = build_app(gw)
    with pytest.raises(RuntimeError):
        async with lifespan(app):
            pass
    assert good.started and good.stopped  # the started worker was cleaned up
