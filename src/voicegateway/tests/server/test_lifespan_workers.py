"""Phase 3, Step 8: the lifespan starts and stops the background workers."""

from __future__ import annotations

import asyncio
import time

import pytest
import yaml
from starlette.testclient import TestClient

from voicegateway.core import events
from voicegateway.core.events import lifespan
from voicegateway.core.gateway import Gateway
from voicegateway.middleware.node_samples_worker_middleware import (
    TARGETS_ENV_VAR,
    NodeSamplesWorker,
)
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


# --- the node scrape worker: opt-in, and off by default ----------------------
#
# Every other worker reads the database it is already attached to. This one
# makes outbound HTTP requests, so "does a default install start it" is the
# question these tests exist to answer, and the answer has to stay "no".

# Port 9 (discard) is reliably closed on loopback: the scrape fails fast with a
# refused connection instead of reaching anything real.
_TARGET = "node-exporter:sfu-1=http://127.0.0.1:9/metrics"


def _node_workers(workers) -> list[NodeSamplesWorker]:
    return [w for w in workers if isinstance(w, NodeSamplesWorker)]


async def test_node_scrape_not_started_when_env_unset(tmp_path, monkeypatch) -> None:
    """The default install: no targets variable, no scrape worker, no traffic."""
    monkeypatch.delenv(TARGETS_ENV_VAR, raising=False)
    gw = _gateway(tmp_path, monkeypatch, {"cost_tracking": {"enabled": True}})
    app = build_app(gw)
    async with lifespan(app):
        assert _node_workers(app.state.workers) == []
        # Unchanged from before this worker was wired in.
        assert len(app.state.workers) == 3


async def test_node_scrape_not_started_when_env_is_blank(tmp_path, monkeypatch) -> None:
    """A set-but-empty variable is still no targets, so still no worker."""
    monkeypatch.setenv(TARGETS_ENV_VAR, "  ,  ")
    gw = _gateway(tmp_path, monkeypatch, {"cost_tracking": {"enabled": True}})
    app = build_app(gw)
    async with lifespan(app):
        assert _node_workers(app.state.workers) == []


async def test_node_scrape_not_started_when_workers_disabled(
    tmp_path, monkeypatch
) -> None:
    """``workers.enabled: false`` is a kill switch for this worker too."""
    monkeypatch.setenv(TARGETS_ENV_VAR, _TARGET)
    gw = _gateway(
        tmp_path,
        monkeypatch,
        {"cost_tracking": {"enabled": True}, "workers": {"enabled": False}},
    )
    app = build_app(gw)
    async with lifespan(app):
        assert app.state.workers == []


async def test_node_scrape_not_started_when_storage_disabled(
    tmp_path, monkeypatch
) -> None:
    """No storage to write samples into means no scrape, targets or not."""
    monkeypatch.setenv(TARGETS_ENV_VAR, _TARGET)
    gw = _gateway(
        tmp_path, monkeypatch, {"cost_tracking": {"enabled": False}}, db=False
    )
    app = build_app(gw)
    async with lifespan(app):
        assert app.state.workers == []


async def test_node_scrape_starts_and_stops_when_targets_configured(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv(TARGETS_ENV_VAR, _TARGET)
    gw = _gateway(tmp_path, monkeypatch, {"cost_tracking": {"enabled": True}})
    app = build_app(gw)
    async with lifespan(app):
        workers = app.state.workers
        assert len(workers) == 4  # the usual three plus the scrape
        node = _node_workers(workers)
        assert len(node) == 1
        assert node[0]._poll_interval == 15  # the default cadence
        task = node[0]._task
        assert task is not None
    assert node[0]._task is None  # stopped on shutdown, like its siblings
    assert task.cancelled()  # cancelled and awaited, so nothing went unretrieved
    assert not [t for t in asyncio.all_tasks() if t.get_name() == "node-samples-worker"]


async def test_node_scrape_interval_is_applied(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(TARGETS_ENV_VAR, _TARGET)
    gw = _gateway(
        tmp_path,
        monkeypatch,
        {
            "cost_tracking": {"enabled": True},
            "workers": {"node_scrape_interval_seconds": 333},
        },
    )
    app = build_app(gw)
    async with lifespan(app):
        assert [w._poll_interval for w in _node_workers(app.state.workers)] == [333]


async def test_node_scrape_shutdown_is_not_blocked_by_a_hanging_target(
    tmp_path, monkeypatch
) -> None:
    """A target that accepts the connection and never answers must not hold up
    shutdown: the worker is cancelled mid-scrape, not waited out."""
    release = asyncio.Event()

    async def _never_answer(_reader, writer) -> None:
        try:
            await release.wait()
        finally:
            writer.close()

    server = await asyncio.start_server(_never_answer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        monkeypatch.setenv(
            TARGETS_ENV_VAR, f"node-exporter:hung=http://127.0.0.1:{port}/metrics"
        )
        gw = _gateway(tmp_path, monkeypatch, {"cost_tracking": {"enabled": True}})
        app = build_app(gw)
        ctx = lifespan(app)
        await ctx.__aenter__()
        node = _node_workers(app.state.workers)
        assert len(node) == 1
        task = node[0]._task
        assert task is not None
        # Let the scrape get in flight and block on the read.
        await asyncio.sleep(0.05)
        assert not task.done()
        started = time.monotonic()
        await ctx.__aexit__(None, None, None)
        elapsed = time.monotonic() - started
    finally:
        release.set()
        server.close()
        await server.wait_closed()
    assert task.cancelled()
    # Well inside the worker's 5 s per-target scrape deadline: shutdown cancels
    # the in-flight request rather than waiting for it to time out.
    assert elapsed < 3.0
