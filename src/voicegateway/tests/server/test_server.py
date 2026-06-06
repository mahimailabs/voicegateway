"""Tests for voicegateway/server.py — HTTP API endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.server import build_app


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "server-test.db"))
    return Gateway(config_path=temp_config)


@pytest.fixture
def app(gateway):
    return build_app(gateway)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health(client):
    from voicegateway import __version__

    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    # Reports the real installed version (local build segment stripped),
    # not a hand-edited string that drifts every release.
    assert data["version"] == __version__.split("+", 1)[0]


async def test_v1_status(client):
    resp = await client.get("/v1/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data
    assert "model_count" in data
    assert "project_count" in data


async def test_v1_status_includes_pricing_sources(client):
    """/v1/status carries the per-modality pricing source subtree."""
    resp = await client.get("/v1/status")
    data = resp.json()
    assert "pricing" in data
    assert "llm" in data["pricing"]
    assert "stt" in data["pricing"]
    assert "tts" in data["pricing"]
    # All three modalities are now priced by voice-prices.
    for modality in ("llm", "stt", "tts"):
        assert data["pricing"][modality]["source"].startswith("voice-prices@")


async def test_v1_models(client):
    resp = await client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data


async def test_v1_models_with_project_filter(client):
    resp = await client.get("/v1/models?project=test-project")
    assert resp.status_code == 200
    data = resp.json()
    assert data["project"] == "test-project"


async def test_v1_costs_empty(client):
    resp = await client.get("/v1/costs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0.0
    assert "by_provider" in data


async def test_v1_costs_with_period(client):
    resp = await client.get("/v1/costs?period=week")
    assert resp.status_code == 200
    assert resp.json()["period"] == "week"


async def test_v1_costs_with_project(client):
    resp = await client.get("/v1/costs?project=test-project")
    assert resp.status_code == 200


async def test_v1_costs_per_modality_excluded_by_default(client):
    """`by_modality` is opt-in via the query param to keep the default response stable."""
    resp = await client.get("/v1/costs")
    assert resp.status_code == 200
    assert "by_modality" not in resp.json()


async def test_v1_costs_per_modality_when_requested(client):
    """`?per_modality=true` adds the breakdown; empty dict when no traffic recorded."""
    resp = await client.get("/v1/costs?per_modality=true")
    assert resp.status_code == 200
    data = resp.json()
    assert "by_modality" in data
    assert data["by_modality"] == {}


async def test_v1_costs_includes_pricing_source_by_default(client, gateway):
    """Q7: include_pricing_source defaults to True since v0.0.5 so the"""
    import time
    import uuid

    from voicegateway.models.request_model import RequestRecord

    await gateway.storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=time.time(),
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="test-project",
            input_units=100,
            output_units=50,
            cost_usd=0.01,
            pricing_source="voice-prices@0.0.8",
        )
    )

    resp = await client.get("/v1/costs?period=today")
    assert resp.status_code == 200
    data = resp.json()
    by_model = data["by_model"]
    assert "openai/gpt-4o-mini" in by_model
    # The attribution string is present without us having to ask
    # for it explicitly.
    assert by_model["openai/gpt-4o-mini"]["pricing_source"] == "voice-prices@0.0.8"


async def test_v1_costs_pricing_source_opt_out(client, gateway):
    """Pass ?include_pricing_source=false to drop the per-row"""
    import time
    import uuid

    from voicegateway.models.request_model import RequestRecord

    await gateway.storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=time.time(),
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="test-project",
            cost_usd=0.01,
            pricing_source="voice-prices@0.0.8",
        )
    )

    resp = await client.get("/v1/costs?period=today&include_pricing_source=false")
    data = resp.json()
    by_model = data["by_model"]
    assert "pricing_source" not in by_model.get("openai/gpt-4o-mini", {})


async def test_v1_costs_include_pricing_source_default_on(client):
    """Q7: include_pricing_source defaults to True since v0.0.5. With"""
    resp = await client.get("/v1/costs")
    assert resp.status_code == 200
    data = resp.json()
    assert "by_model" in data


async def test_v1_costs_include_pricing_source_explicit_true(client):
    """`?include_pricing_source=true` is accepted (matches the new default)."""
    resp = await client.get("/v1/costs?include_pricing_source=true")
    assert resp.status_code == 200
    data = resp.json()
    assert "by_model" in data


async def test_v1_costs_with_iso_date_window(client):
    """`?start=` and `?end=` ISO dates are accepted; response stays valid."""
    resp = await client.get("/v1/costs?start=2026-05-01&end=2026-05-04")
    assert resp.status_code == 200
    data = resp.json()
    assert "by_model" in data
    assert "by_provider" in data


async def test_v1_costs_with_invalid_iso_date_returns_400(client):
    """Malformed `?start=` returns 400 with a helpful error message."""
    resp = await client.get("/v1/costs?start=not-a-date")
    assert resp.status_code == 400
    assert "YYYY-MM-DD" in resp.json()["detail"]


async def test_v1_costs_with_only_start_or_only_end(client):
    """Either bound is independently optional; missing bound is open-ended."""
    resp = await client.get("/v1/costs?start=2026-05-01")
    assert resp.status_code == 200
    resp = await client.get("/v1/costs?end=2026-05-04")
    assert resp.status_code == 200


async def test_v1_costs_period_with_window_emits_deprecation_header(client):
    """Mixing legacy `period` with new `start`/`end` surfaces the"""
    resp = await client.get("/v1/costs?period=week&start=2026-05-01&end=2026-05-04")
    assert resp.status_code == 200
    assert "deprecation" in {k.lower() for k in resp.headers}
    msg = resp.headers["deprecation"].lower()
    assert "period" in msg and "start/end" in msg


async def test_v1_costs_period_with_window_exposes_deprecation_to_cors(client):
    """The CORS preflight must list `Deprecation` in"""
    resp = await client.get(
        "/v1/costs?period=week&start=2026-05-01&end=2026-05-04",
        headers={"origin": "http://localhost:3000"},
    )
    assert resp.status_code == 200
    expose = resp.headers.get("access-control-expose-headers", "")
    assert "Deprecation" in expose, (
        f"Deprecation must be exposed via CORS; got {expose!r}"
    )


async def test_v1_costs_period_without_window_no_deprecation_header(client):
    """Legacy callers that pass only `period` see no Deprecation header."""
    resp = await client.get("/v1/costs?period=week")
    assert resp.status_code == 200
    assert "deprecation" not in {k.lower() for k in resp.headers}


async def test_v1_costs_window_without_period_no_deprecation_header(client):
    """New-API callers that pass only `start`/`end` see no Deprecation header."""
    resp = await client.get("/v1/costs?start=2026-05-01&end=2026-05-04")
    assert resp.status_code == 200
    assert "deprecation" not in {k.lower() for k in resp.headers}


async def test_v1_costs_no_params_defaults_to_today(client):
    """Backward compat: omitting all three falls back to period=today."""
    resp = await client.get("/v1/costs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["period"] == "today"
    # No deprecation header for the no-params path.
    assert "deprecation" not in {k.lower() for k in resp.headers}


async def test_v1_costs_combined_query_params(client, gateway):
    """All three new params (per_modality, include_pricing_source, start/end) compose."""
    import datetime as _dt
    import uuid

    from voicegateway.models.request_model import RequestRecord

    today = _dt.date.today()
    in_window = _dt.datetime.combine(today, _dt.time(12, 0)).timestamp()
    out_of_window = _dt.datetime.combine(
        today - _dt.timedelta(days=10), _dt.time(12, 0)
    ).timestamp()
    await gateway.storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=in_window,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            cost_usd=0.10,
            pricing_source="voice-prices@0.0.8",
        )
    )
    await gateway.storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=in_window,
            modality="stt",
            model_id="deepgram/nova-3",
            provider="deepgram",
            cost_usd=0.05,
            pricing_source="local-stt@2026-05-04",
        )
    )
    await gateway.storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=in_window,
            modality="tts",
            model_id="cartesia/sonic-3",
            provider="cartesia",
            cost_usd=0.03,
            pricing_source="local-tts@2026-05-04",
        )
    )
    await gateway.storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=out_of_window,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            cost_usd=99.0,
            pricing_source="voice-prices@0.0.8",
        )
    )

    start = (today - _dt.timedelta(days=1)).isoformat()
    end = today.isoformat()
    resp = await client.get(
        "/v1/costs"
        f"?per_modality=true&include_pricing_source=true&start={start}&end={end}"
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["total"] == pytest.approx(0.18, abs=0.001)
    assert data["by_modality"].keys() == {"llm", "stt", "tts"}
    assert data["by_modality"]["llm"]["cost"] == pytest.approx(0.10, abs=0.001)
    assert (
        data["by_model"]["openai/gpt-4o-mini"]["pricing_source"] == "voice-prices@0.0.8"
    )
    assert (
        data["by_model"]["deepgram/nova-3"]["pricing_source"] == "local-stt@2026-05-04"
    )
    # The 99.0 out-of-window record must not leak into any aggregate.
    assert data["by_modality"]["llm"]["requests"] == 1
    assert data["by_provider"]["openai"]["requests"] == 1


async def test_v1_costs_window_overrides_period_at_data_layer(client, gateway):
    """Precedence rule: when both period and start/end are passed,"""
    import datetime as _dt
    import uuid

    from voicegateway.models.request_model import RequestRecord

    today = _dt.date.today()
    # Record from 5 days ago - outside `period=today` but inside the
    # 10-day explicit window below.
    five_days_ago = _dt.datetime.combine(
        today - _dt.timedelta(days=5), _dt.time(12, 0)
    ).timestamp()
    await gateway.storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=five_days_ago,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            cost_usd=0.42,
            pricing_source="voice-prices@0.0.8",
        )
    )

    start = (today - _dt.timedelta(days=10)).isoformat()
    end = today.isoformat()
    # period=today would alone exclude the record; start/end must
    # win and the response surfaces the cost.
    resp = await client.get(f"/v1/costs?period=today&start={start}&end={end}")
    assert resp.status_code == 200
    data = resp.json()
    # If period had won, total would be 0 (no records today). The
    # 5-days-ago record's $0.42 must be in the total.
    assert data["total"] == pytest.approx(0.42, abs=0.001)
    # Deprecation header still set (already covered by the
    # dedicated test, but checked here for completeness so this
    # test fails loudly if precedence and header behaviors drift
    # apart).
    assert "deprecation" in {k.lower() for k in resp.headers}


async def test_v1_latency_empty(client):
    resp = await client.get("/v1/latency")
    assert resp.status_code == 200


async def test_v1_projects(client):
    resp = await client.get("/v1/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert "projects" in data
    project_ids = [p["id"] for p in data["projects"]]
    assert "test-project" in project_ids


async def test_v1_project_detail(client):
    resp = await client.get("/v1/projects/test-project")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "test-project"
    assert data["daily_budget"] == 10.0
    assert "budget_action" in data
    assert "budget_status" in data
    assert "today_spend" in data


async def test_v1_project_not_found(client):
    resp = await client.get("/v1/projects/nonexistent")
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data


async def test_v1_logs_empty(client):
    resp = await client.get("/v1/logs")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_v1_logs_with_filters(client):
    resp = await client.get("/v1/logs?limit=10&modality=stt")
    assert resp.status_code == 200


async def test_v1_metrics(client):
    resp = await client.get("/v1/metrics")
    assert resp.status_code == 200
    assert "voicegw_uptime_seconds" in resp.text
    assert "voicegw_providers_configured" in resp.text


def _midday_today() -> float:
    import datetime as _dt

    return _dt.datetime.combine(_dt.date.today(), _dt.time(12, 0)).timestamp()


async def test_v1_latency_includes_percentiles(client, gateway):
    """/v1/latency now returns percentile buckets per model."""
    import uuid

    from voicegateway.models.request_model import RequestRecord

    now = _midday_today()
    for i in range(1, 21):
        await gateway.storage.log_request(
            RequestRecord(
                id=str(uuid.uuid4()),
                timestamp=now - i,
                modality="stt",
                model_id="deepgram/nova-3",
                provider="deepgram",
                ttfb_ms=float(i * 10),
                total_latency_ms=float(i * 20),
            )
        )
    resp = await client.get("/v1/latency")
    data = resp.json()
    assert "deepgram/nova-3" in data
    entry = data["deepgram/nova-3"]
    assert set(entry["ttfb_percentiles"].keys()) == {"p50", "p95", "p99"}
    assert entry["ttfb_percentiles"]["p95"] > entry["ttfb_percentiles"]["p50"]


async def test_v1_metrics_emits_latency_summary(client, gateway):
    import uuid

    from voicegateway.models.request_model import RequestRecord

    now = _midday_today()
    for i in range(1, 11):
        await gateway.storage.log_request(
            RequestRecord(
                id=str(uuid.uuid4()),
                timestamp=now - i,
                modality="llm",
                model_id="openai/gpt-4o-mini",
                provider="openai",
                ttfb_ms=float(i * 50),
                total_latency_ms=float(i * 100),
            )
        )
    resp = await client.get("/v1/metrics")
    text = resp.text
    assert "voicegw_request_ttfb_seconds" in text
    assert "voicegw_request_total_latency_seconds" in text
    assert 'quantile="0.5"' in text
    assert 'quantile="0.95"' in text
    assert 'quantile="0.99"' in text
    assert 'model="openai/gpt-4o-mini"' in text


# --------------------------------------------------------------------
# CRUD — Providers
# --------------------------------------------------------------------


async def test_list_providers(client):
    resp = await client.get("/v1/providers")
    assert resp.status_code == 200
    assert "providers" in resp.json()


async def test_create_provider(client):
    resp = await client.post(
        "/v1/providers",
        json={
            "provider_id": "ollama-test",
            "provider_type": "ollama",
            "api_key": "",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["source"] == "db"


async def test_create_provider_yaml_conflict(client):
    resp = await client.post(
        "/v1/providers",
        json={
            "provider_id": "openai",
            "provider_type": "openai",
            "api_key": "sk-test",
        },
    )
    assert resp.status_code == 409


async def test_create_provider_bad_type(client):
    resp = await client.post(
        "/v1/providers",
        json={
            "provider_id": "bad",
            "provider_type": "nonexistent",
            "api_key": "",
        },
    )
    assert resp.status_code == 400


async def test_delete_provider_preview(client):
    await client.post(
        "/v1/providers",
        json={
            "provider_id": "del-me",
            "provider_type": "ollama",
            "api_key": "",
        },
    )
    resp = await client.delete("/v1/providers/del-me")
    assert resp.status_code == 200
    assert "would_delete" in resp.json()


async def test_delete_provider_confirm(client):
    await client.post(
        "/v1/providers",
        json={
            "provider_id": "del-me2",
            "provider_type": "ollama",
            "api_key": "",
        },
    )
    resp = await client.delete("/v1/providers/del-me2?confirm=true")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == "del-me2"


async def test_delete_provider_yaml_forbidden(client):
    resp = await client.delete("/v1/providers/openai?confirm=true")
    assert resp.status_code == 403


async def test_patch_provider(client):
    await client.post(
        "/v1/providers",
        json={
            "provider_id": "patch-me",
            "provider_type": "ollama",
            "api_key": "",
        },
    )
    resp = await client.patch(
        "/v1/providers/patch-me", json={"base_url": "http://new:11434"}
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] is True


async def test_test_provider_not_found(client):
    resp = await client.post("/v1/providers/nonexistent/test")
    assert resp.status_code == 404


# --------------------------------------------------------------------
# CRUD — Models
# --------------------------------------------------------------------


async def test_create_model(client):
    resp = await client.post(
        "/v1/models",
        json={
            "modality": "llm",
            "provider_id": "openai",
            "model_name": "gpt-5-test",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["model_id"] == "openai/gpt-5-test"


async def test_create_model_yaml_conflict(client):
    resp = await client.post(
        "/v1/models",
        json={
            "modality": "llm",
            "provider_id": "openai",
            "model_name": "gpt-4o-mini",
        },
    )
    assert resp.status_code == 409


async def test_delete_model_yaml_forbidden(client):
    resp = await client.delete("/v1/models/deepgram/nova-3?confirm=true")
    assert resp.status_code == 403


async def test_delete_model_confirm(client):
    await client.post(
        "/v1/models",
        json={
            "modality": "llm",
            "provider_id": "openai",
            "model_name": "to-delete",
        },
    )
    resp = await client.delete("/v1/models/openai/to-delete?confirm=true")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == "openai/to-delete"


# --------------------------------------------------------------------
# CRUD — Projects
# --------------------------------------------------------------------


async def test_create_project(client):
    resp = await client.post(
        "/v1/projects",
        json={
            "project_id": "http-proj",
            "name": "HTTP Project",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["source"] == "db"


async def test_create_project_conflict(client):
    resp = await client.post(
        "/v1/projects",
        json={
            "project_id": "test-project",
            "name": "dup",
        },
    )
    assert resp.status_code == 409


async def test_patch_project(client):
    await client.post(
        "/v1/projects",
        json={
            "project_id": "update-me",
            "name": "Original",
        },
    )
    resp = await client.patch("/v1/projects/update-me", json={"name": "Updated"})
    assert resp.status_code == 200
    assert resp.json()["updated"] is True


async def test_delete_project_yaml_forbidden(client):
    resp = await client.delete("/v1/projects/test-project?confirm=true")
    assert resp.status_code == 403


async def test_delete_project_confirm(client):
    await client.post(
        "/v1/projects",
        json={
            "project_id": "kill-me",
            "name": "K",
        },
    )
    resp = await client.delete("/v1/projects/kill-me?confirm=true")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == "kill-me"


# --------------------------------------------------------------------
# Audit log
# --------------------------------------------------------------------


async def test_audit_log_records_crud(client):
    await client.post(
        "/v1/providers",
        json={
            "provider_id": "audit-test",
            "provider_type": "ollama",
            "api_key": "",
        },
    )
    resp = await client.get("/v1/audit-log?entity_type=provider")
    assert resp.status_code == 200
    entries = resp.json()
    assert any(e["entity_id"] == "audit-test" for e in entries)


async def test_audit_log_empty(client):
    resp = await client.get("/v1/audit-log?entity_type=nonexistent")
    assert resp.status_code == 200
    assert resp.json() == []
