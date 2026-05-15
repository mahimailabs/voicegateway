"""Protocol contract tests for the v0.1.1 TUI data layer."""

from __future__ import annotations

import time
import uuid

import httpx
import pytest

from voicegateway.cli.tui.data import MetricsClient
from voicegateway.cli.tui.data.exceptions import LocalModeUnsupportedError
from voicegateway.cli.tui.data.factory import _resolve_db_path, make_client
from voicegateway.cli.tui.data.http import HttpClient
from voicegateway.cli.tui.data.local import LocalClient
from voicegateway.middleware.cost_tracker_middleware import RequestRecord
from voicegateway.services.storage_service import StorageService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_transport():
    """Daemon stand-in that returns canonical /v1/* responses."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/sessions":
            return httpx.Response(
                200,
                json=[
                    {"id": "vg-1", "total_cost_usd": 0.01, "request_count": 3},
                    {"id": "vg-2", "total_cost_usd": 0.02, "request_count": 5},
                ],
            )
        if path == "/v1/sessions/vg-1":
            return httpx.Response(
                200, json={"id": "vg-1", "total_cost_usd": 0.01, "by_modality": {}}
            )
        if path == "/v1/sessions/missing":
            return httpx.Response(404, json={"detail": "not found"})
        if path == "/v1/costs":
            return httpx.Response(
                200,
                json={
                    "period": "today",
                    "total": 0.5,
                    "by_modality": {
                        "stt": {"cost": 0.1},
                        "llm": {"cost": 0.3},
                        "tts": {"cost": 0.1},
                    },
                },
            )
        if path == "/v1/logs":
            return httpx.Response(200, json=[{"id": "r1", "modality": "llm"}])
        if path == "/v1/providers":
            return httpx.Response(200, json=[{"provider_id": "openai", "status": "ok"}])
        if path == "/v1/providers/openai/test":
            return httpx.Response(200, json={"status": "ok", "latency_ms": 120})
        return httpx.Response(500, json={"detail": "unexpected"})

    return httpx.MockTransport(handler)


@pytest.fixture
def http_client(mock_transport):
    inner = httpx.AsyncClient(base_url="http://daemon.test", transport=mock_transport)
    return HttpClient(url="http://daemon.test", token="tkn", http_client=inner)


@pytest.fixture
async def populated_storage(tmp_path):
    """StorageService seeded with two sessions + one managed provider."""
    db = tmp_path / "voicegw.db"
    storage = StorageService(db)
    if hasattr(storage, "init_db"):
        await storage.init_db()
    for i in range(2):
        rec = RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=time.time(),
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="default",
            input_units=10,
            output_units=5,
            cost_usd=0.001,
            metadata={},
            session_id=f"vg-s{i}",
        )
        await storage.log_request(rec)
    await storage.upsert_managed_provider(
        provider_id="p1",
        provider_type="openai",
        api_key="sk-x",
        project="default",
    )
    return db, storage


@pytest.fixture
async def local_client(populated_storage):
    db, storage = populated_storage
    return LocalClient(db_path=db, storage=storage)


# ---------------------------------------------------------------------------
# Protocol conformance (both clients structurally satisfy MetricsClient)
# ---------------------------------------------------------------------------


_PROTOCOL_METHODS: tuple[str, ...] = (
    "list_sessions",
    "get_session_detail",
    "list_costs",
    "list_logs",
    "list_providers",
    "test_provider",
)


def test_http_client_is_metrics_client(http_client):
    """``HttpClient`` passes ``isinstance(_, MetricsClient)``."""
    assert isinstance(http_client, MetricsClient)


async def test_local_client_is_metrics_client(local_client):
    assert isinstance(local_client, MetricsClient)


@pytest.mark.parametrize("method_name", _PROTOCOL_METHODS)
def test_http_client_exposes_required_methods(http_client, method_name):
    assert callable(getattr(http_client, method_name))


@pytest.mark.parametrize("method_name", _PROTOCOL_METHODS)
def test_local_client_exposes_required_methods(method_name, tmp_path):
    client = LocalClient(db_path=tmp_path / "voicegw.db")
    assert callable(getattr(client, method_name))


# ---------------------------------------------------------------------------
# HttpClient method coverage
# ---------------------------------------------------------------------------


async def test_http_list_sessions_returns_rows(http_client):
    rows = await http_client.list_sessions(limit=10)
    assert len(rows) == 2
    assert rows[0]["id"] == "vg-1"


async def test_http_list_sessions_passes_filters(http_client):
    rows = await http_client.list_sessions(limit=5, project="p1", order_by="cost_desc")
    # MockTransport ignores the params; we just confirm the call shape
    # does not raise and the response list is returned verbatim.
    assert isinstance(rows, list)


async def test_http_get_session_detail_known(http_client):
    detail = await http_client.get_session_detail("vg-1")
    assert detail is not None
    assert detail["id"] == "vg-1"


async def test_http_get_session_detail_404_returns_none(http_client):
    assert await http_client.get_session_detail("missing") is None


async def test_http_list_costs(http_client):
    costs = await http_client.list_costs(period="today", include_pricing_source=True)
    assert costs["total"] == 0.5
    assert costs["by_modality"]["stt"]["cost"] == 0.1


async def test_http_list_logs(http_client):
    logs = await http_client.list_logs(limit=5)
    assert logs == [{"id": "r1", "modality": "llm"}]


async def test_http_list_providers(http_client):
    providers = await http_client.list_providers()
    assert providers[0]["provider_id"] == "openai"


async def test_http_test_provider(http_client):
    result = await http_client.test_provider("openai")
    assert result["status"] == "ok"
    assert result["latency_ms"] == 120


def test_http_empty_token_treated_as_none():
    """``--token ""`` must not produce ``Bearer ``; daemon would 401."""
    c = HttpClient(url="http://x", token="")
    assert c._auth_headers() == {}


def test_http_token_produces_bearer_header():
    c = HttpClient(url="http://x", token="secret")
    assert c._auth_headers() == {"Authorization": "Bearer secret"}


def test_http_default_poll_is_one_second():
    """Locked decision 1: Gateway-mode polling cadence is 1 s."""
    c = HttpClient(url="http://x")
    assert c.poll_seconds == 1.0


async def test_http_owns_internal_client_by_default():
    c = HttpClient(url="http://x")
    assert c._owns_client is True
    await c.aclose()


async def test_http_skips_close_on_injected_client(mock_transport):
    inner = httpx.AsyncClient(base_url="http://daemon.test", transport=mock_transport)
    c = HttpClient(url="http://daemon.test", http_client=inner)
    assert c._owns_client is False
    await c.aclose()
    # Inner client still usable after wrapper close.
    response = await inner.get("/v1/sessions")
    assert response.status_code == 200
    await inner.aclose()


async def test_http_async_context_manager_closes(mock_transport):
    inner = httpx.AsyncClient(base_url="http://daemon.test", transport=mock_transport)
    async with HttpClient(url="http://daemon.test", http_client=inner) as c:
        assert isinstance(c, HttpClient)
    # No assertion on inner state -- the contract is "no exception on
    # __aexit__"; ownership rules are tested separately.
    await inner.aclose()


# ---------------------------------------------------------------------------
# LocalClient method coverage
# ---------------------------------------------------------------------------


async def test_local_list_sessions(local_client):
    sessions = await local_client.list_sessions(limit=10)
    assert len(sessions) == 2
    assert sessions[0]["id"] in ("vg-s0", "vg-s1")


async def test_local_get_session_detail_known(local_client):
    detail = await local_client.get_session_detail("vg-s0")
    assert detail is not None
    assert detail["id"] == "vg-s0"


async def test_local_get_session_detail_unknown_returns_none(local_client):
    assert await local_client.get_session_detail("nope") is None


async def test_local_list_costs_carries_by_modality(local_client):
    costs = await local_client.list_costs(period="today")
    assert "by_modality" in costs
    assert isinstance(costs["by_modality"], dict)


async def test_local_list_logs(local_client):
    logs = await local_client.list_logs(limit=10)
    assert len(logs) == 2


async def test_local_list_providers(local_client):
    providers = await local_client.list_providers()
    assert any(p.get("provider_id") == "p1" for p in providers)


async def test_local_list_providers_filters_by_project(local_client):
    matched = await local_client.list_providers(project="default")
    other = await local_client.list_providers(project="other")
    assert len(matched) == 1
    assert other == []


async def test_local_test_provider_raises_with_feature_attribute(
    local_client,
):
    """Locked decision 4: write paths in Local mode raise"""
    with pytest.raises(LocalModeUnsupportedError) as exc_info:
        await local_client.test_provider("p1")
    assert exc_info.value.feature == "test_provider"
    assert "test_provider" in str(exc_info.value)


def test_local_default_poll_is_five_seconds(tmp_path):
    """Locked decision 2: Local-mode polling cadence is 5 s."""
    c = LocalClient(db_path=tmp_path / "voicegw.db")
    assert c.poll_seconds == 5.0


async def test_local_aclose_is_no_op(local_client):
    """StorageService opens connections per-call; no global resource."""
    await local_client.aclose()  # must not raise
    # Methods still work after close because the storage adapter
    # opens a fresh connection per await.
    sessions = await local_client.list_sessions(limit=1)
    assert isinstance(sessions, list)


async def test_local_async_context_manager(populated_storage):
    db, storage = populated_storage
    async with LocalClient(db_path=db, storage=storage) as c:
        assert isinstance(c, LocalClient)


# ---------------------------------------------------------------------------
# Factory dispatch
# ---------------------------------------------------------------------------


def test_factory_local_true_returns_local_client(tmp_path):
    c = make_client(local=True, url="http://daemon.test", db_path=tmp_path / "x.db")
    assert isinstance(c, LocalClient)


def test_factory_local_false_returns_http_client():
    c = make_client(local=False, url="http://daemon.test")
    assert isinstance(c, HttpClient)


def test_factory_local_true_overrides_reachable_daemon(tmp_path):
    """Locked decision 5: ``--local`` always wins regardless of url."""
    c = make_client(
        local=True,
        url="http://reachable-daemon.example",
        db_path=tmp_path / "x.db",
    )
    assert isinstance(c, LocalClient)


def test_factory_local_default_poll_is_five_seconds(tmp_path):
    c = make_client(local=True, url="", db_path=tmp_path / "x.db")
    assert c.poll_seconds == 5.0


def test_factory_gateway_default_poll_is_one_second():
    c = make_client(local=False, url="http://x")
    assert c.poll_seconds == 1.0


def test_factory_explicit_poll_overrides_local_default(tmp_path):
    c = make_client(local=True, url="", db_path=tmp_path / "x.db", poll=2.5)
    assert c.poll_seconds == 2.5


def test_factory_explicit_poll_overrides_gateway_default():
    c = make_client(local=False, url="http://x", poll=0.25)
    assert c.poll_seconds == 0.25


def test_factory_db_path_explicit_arg(tmp_path):
    path = tmp_path / "explicit.db"
    c = make_client(local=True, url="", db_path=path)
    assert c._db_path == path


def test_factory_db_path_env_var_resolution(tmp_path, monkeypatch):
    path = tmp_path / "from-env.db"
    monkeypatch.setenv("VOICEGW_DB_PATH", str(path))
    c = make_client(local=True, url="", db_path=None)
    assert str(c._db_path) == str(path)


def test_factory_db_path_env_overrides_explicit(tmp_path, monkeypatch):
    """``$VOICEGW_DB_PATH`` is the top-priority debugging escape hatch"""
    env_path = tmp_path / "from-env.db"
    yaml_path = tmp_path / "from-yaml.db"
    monkeypatch.setenv("VOICEGW_DB_PATH", str(env_path))
    c = make_client(local=True, url="", db_path=yaml_path)
    assert str(c._db_path) == str(env_path)


def test_factory_db_path_default(monkeypatch):
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    path = _resolve_db_path(None)
    assert str(path).endswith(".config/voicegateway/voicegw.db")


def test_factory_db_path_expands_user(monkeypatch):
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    path = _resolve_db_path("~/some/place.db")
    # ``~`` must be expanded so StorageService doesn't try to mkdir the
    # literal ``~`` directory under cwd.
    assert "~" not in str(path)


# ---------------------------------------------------------------------------
# HttpClient connection-state tracking (REQ-VG-TUI-007 reconnection)
# ---------------------------------------------------------------------------


async def test_http_client_starts_connected() -> None:
    """``is_connected`` defaults True so the indicator does not flash"""
    c = HttpClient(url="http://x")
    assert c.is_connected is True
    await c.aclose()


async def test_http_client_marks_disconnected_on_connect_error() -> None:
    """A ConnectError flips ``is_connected`` to False before"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    inner = httpx.AsyncClient(
        base_url="http://daemon.test",
        transport=httpx.MockTransport(handler),
    )
    client = HttpClient(url="http://daemon.test", http_client=inner)
    with pytest.raises(httpx.ConnectError):
        await client.list_sessions()
    assert client.is_connected is False
    assert client._last_error is not None
    assert "Connection refused" in client._last_error


async def test_http_client_marks_connected_on_recovery() -> None:
    """After a failure, the next successful round-trip flips"""
    state = {"fail": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json=[])

    inner = httpx.AsyncClient(
        base_url="http://daemon.test",
        transport=httpx.MockTransport(handler),
    )
    client = HttpClient(url="http://daemon.test", http_client=inner)
    with pytest.raises(httpx.ConnectError):
        await client.list_sessions()
    assert client.is_connected is False

    state["fail"] = False
    rows = await client.list_sessions()
    assert rows == []
    assert client.is_connected is True
    assert client._last_error is None


async def test_http_client_status_error_does_not_flip_connected() -> None:
    """A 4xx/5xx response means the daemon answered but rejected the"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "unauthorized"})

    inner = httpx.AsyncClient(
        base_url="http://daemon.test",
        transport=httpx.MockTransport(handler),
    )
    client = HttpClient(url="http://daemon.test", http_client=inner)
    with pytest.raises(httpx.HTTPStatusError):
        await client.test_provider("openai")
    # Daemon responded; connection is fine.
    assert client.is_connected is True
    assert client._last_error is None


async def test_http_client_marks_disconnected_on_timeout() -> None:
    """ReadTimeout is in the connection-error tuple; same contract."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout")

    inner = httpx.AsyncClient(
        base_url="http://daemon.test",
        transport=httpx.MockTransport(handler),
    )
    client = HttpClient(url="http://daemon.test", http_client=inner)
    with pytest.raises(httpx.ReadTimeout):
        await client.list_logs()
    assert client.is_connected is False
