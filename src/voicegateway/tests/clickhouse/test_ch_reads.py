"""chDB unit tests for the ClickHouse tenant-scoped read repository.

These tests run entirely in-process via chDB (no Docker required, no
pytest.mark.integration). The read functions accept a clickhouse-connect-style
async client; here we bridge that interface to the synchronous chDB session
via a tiny ChdbAdapter defined in this file (test code only, not shipped in
production).

Data layout used in all tests:
  - 'acme' tenant: 3 requests across two sessions, two providers
  - 'beta' tenant: 1 request in its own session
  - ''    tenant: 1 request (default tenant, no tenant_id set)
"""

from __future__ import annotations

import json
import pathlib
import re
import time
from datetime import UTC

import pytest

from voicegateway.clickhouse import read_repository as rr

MIGRATIONS_DIR = (
    pathlib.Path(__file__).parent.parent.parent / "clickhouse" / "migrations"
)

try:
    from chdb import session as chdb_session

    CHDB_AVAILABLE = True
except ImportError:
    CHDB_AVAILABLE = False

pytestmark = pytest.mark.skipif(not CHDB_AVAILABLE, reason="chdb not installed")


# ---------------------------------------------------------------------------
# chDB session fixture + adapter (test file only -- NOT production code)
#
# The read repository functions take a clickhouse-connect AsyncClient with:
#   result = await client.query(sql, parameters={...})
#   result.result_rows  ->  list[tuple]
#
# chDB is sync and does not support server-side bind params, so the adapter
# replaces every {name:Type} placeholder with the literal value (safe here
# because we control 100% of the test parameters).
# ---------------------------------------------------------------------------


@pytest.fixture
def ch_session_reads(tmp_path):
    """Fresh chDB session with all migrations applied."""
    sess = chdb_session.Session(str(tmp_path / "ch_reads"))
    from voicegateway.clickhouse.migrate import apply_migrations_to_session

    apply_migrations_to_session(sess, MIGRATIONS_DIR)
    yield sess
    sess.close()


def _apply_params(sql: str, parameters: dict) -> str:
    """Replace {name:Type} placeholders with literal values for chDB."""

    def _replace(m):
        name = m.group(1)
        if name not in parameters:
            return m.group(0)
        val = parameters[name]
        if val is None:
            return "NULL"
        if isinstance(val, str):
            escaped = val.replace("'", "''")
            return f"'{escaped}'"
        if isinstance(val, float):
            return repr(val)
        if isinstance(val, int):
            return str(val)
        # datetime or anything else: stringify with quotes
        return f"'{val}'"

    return re.sub(r"\{(\w+):[^}]+\}", _replace, sql)


class _ChdbQueryResult:
    """Mimics clickhouse-connect QueryResult enough for the read repository."""

    def __init__(self, rows: list[tuple]):
        self.result_rows = rows


class ChdbAdapter:
    """Sync chDB session wrapped in an async-compatible .query()."""

    def __init__(self, sess):
        self._sess = sess

    async def query(self, sql: str, parameters: dict | None = None) -> _ChdbQueryResult:
        resolved = _apply_params(sql, parameters or {})
        result = self._sess.query(resolved, "JSONCompact")
        raw = result.bytes() if hasattr(result, "bytes") else bytes(result)
        text = raw.decode().strip()
        if not text:
            return _ChdbQueryResult([])
        parsed = json.loads(text)
        return _ChdbQueryResult([tuple(row) for row in parsed.get("data", [])])


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

# Epoch seconds anchors for seed data
_NOW = int(time.time())
_DAY0 = _NOW - 86400 * 2
_DAY1 = _NOW - 86400
#: The default query window: everything from just before _DAY0 onwards.
_WIN = {"since": float(_DAY0 - 1), "until": None}


def _ts(epoch: int) -> str:
    """Format epoch seconds as ClickHouse DateTime64 string."""
    from datetime import datetime

    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%d %H:%M:%S.000")


def _row(id: str, **kw) -> dict:
    """An acme LLM request on _DAY1 (openai/gpt-4o-mini), overridable."""
    return {
        "tenant_id": "acme",
        "id": id,
        "ts": _DAY1,
        "modality": "llm",
        "provider": "openai",
        "model_id": "openai/gpt-4o-mini",
        "cost_usd": 0.01,
        "ttfb_ms": 100.0,
        "total_latency_ms": 200.0,
        "session_id": "sess",
        "agent_id": "agent-1",
        **kw,
    }


def _insert(sess, rows: list[dict]) -> None:
    """Insert a list of request dicts into telemetry.requests."""
    for row in rows:
        meta = json.dumps(row.get("metadata", {}))
        sess.query(
            f"""
            INSERT INTO telemetry.requests
              (tenant_id, id, timestamp, project, modality, provider, model_id,
               input_units, output_units, cached_input_units, cost_usd,
               pricing_source, ttfb_ms, total_latency_ms, status,
               session_id, agent_id, metadata)
            VALUES
              ('{row["tenant_id"]}', '{row["id"]}', '{_ts(row["ts"])}',
               '{row.get("project", "default")}', '{row["modality"]}',
               '{row["provider"]}', '{row["model_id"]}',
               {row.get("input_units", 100)}, {row.get("output_units", 50)},
               {row.get("cached_input_units", 0)}, {row["cost_usd"]},
               '{row.get("pricing_source", "voice-prices")}',
               {row["ttfb_ms"] if row.get("ttfb_ms") is not None else "NULL"},
               {row["total_latency_ms"] if row.get("total_latency_ms") is not None else "NULL"},
               '{row.get("status", "success")}',
               '{row.get("session_id", "")}',
               '{row.get("agent_id", "")}',
               '{meta}')
            """,
            "CSV",
        )


# Seed dataset: 'acme'=3, 'beta'=1, ''=1
_SEED_ROWS = [
    _row("req-acme-1", ts=_DAY0, session_id="sess-acme-a", metadata={"k": "v"}),
    _row(
        "req-acme-2",
        modality="stt",
        model_id="openai/whisper-1",
        cost_usd=0.02,
        ttfb_ms=150.0,
        total_latency_ms=300.0,
        session_id="sess-acme-a",
    ),
    _row(
        "req-acme-3",
        modality="stt",
        provider="deepgram",
        model_id="deepgram/nova-2",
        cost_usd=0.03,
        ttfb_ms=None,
        total_latency_ms=None,
        session_id="sess-acme-b",
        agent_id="agent-2",
    ),
    _row(
        "req-beta-1",
        tenant_id="beta",
        provider="anthropic",
        model_id="anthropic/claude-3-haiku",
        cost_usd=0.05,
        ttfb_ms=80.0,
        total_latency_ms=160.0,
        session_id="sess-beta-c",
        agent_id="agent-b",
    ),
    _row(
        "req-default-1",
        tenant_id="",
        modality="tts",
        provider="cartesia",
        model_id="cartesia/sonic",
        cost_usd=0.007,
        ttfb_ms=50.0,
        total_latency_ms=120.0,
        session_id="sess-default",
        agent_id="",
    ),
]


@pytest.fixture
def seeded_client(ch_session_reads):
    """ChdbAdapter over a session holding all seed rows."""
    _insert(ch_session_reads, _SEED_ROWS)
    return ChdbAdapter(ch_session_reads)


# ---------------------------------------------------------------------------
# Shape: every read returns non-empty entries carrying the documented keys
# ---------------------------------------------------------------------------

_REQUEST_KEYS = {
    "id", "timestamp", "project", "modality", "model_id", "provider",
    "input_units", "output_units", "cached_input_units", "cost_usd",
    "pricing_source", "ttfb_ms", "total_latency_ms", "status",
    "fallback_from", "error_message", "metadata", "session_id",
    "tenant_id", "agent_id",
}  # fmt: skip
_COST_REQ = {"cost", "requests"}
_LATENCY_KEYS = {
    "avg_ttfb_ms", "avg_latency_ms", "request_count",
    "ttfb_percentiles", "latency_percentiles",
}  # fmt: skip
_SESSION_KEYS = {
    "id", "started_at", "ended_at", "total_cost_usd",
    "request_count", "tenant_id", "agent_id",
}  # fmt: skip
_ACME = {"tenant": "acme", **_WIN}
_SUMMARY_KEYS = {"period", "project", "total", "by_provider", "by_model", "by_project"}

# (reader, kwargs, sub-key to descend into or None, required keys per entry)
_SHAPES = [
    ("get_cost_summary", {**_ACME, "project": "default"}, None, _SUMMARY_KEYS),
    ("get_cost_summary", _ACME, "by_provider", _COST_REQ),
    ("get_cost_summary", _ACME, "by_model", _COST_REQ),
    ("get_cost_summary", _ACME, "by_project", _COST_REQ),
    ("get_cost_by_day", _ACME, None, {"day", "cost", "requests"}),
    ("get_latency_stats", _ACME, None, _LATENCY_KEYS),
    ("get_recent_requests", _ACME, None, _REQUEST_KEYS),
    ("list_sessions", {"tenant": "acme"}, None, _SESSION_KEYS),
    ("get_cost_by_tenant_admin", _WIN, None, _COST_REQ),
]


@pytest.mark.parametrize(
    ("reader", "kwargs", "sub", "keys"),
    _SHAPES,
    ids=[f"{r}-{s or 'top'}" for r, _, s, _k in _SHAPES],
)
async def test_read_shape(seeded_client, reader, kwargs, sub, keys):
    result = await getattr(rr, reader)(seeded_client, **kwargs)
    if reader == "get_cost_summary" and sub is None:
        assert result["project"] == "default"
        entries = [result]
    elif reader in {"get_cost_by_day", "get_recent_requests", "list_sessions"}:
        assert isinstance(result, list)
        entries = result
    else:
        result = result[sub] if sub else result
        assert isinstance(result, dict)
        entries = list(result.values())
    assert entries, f"{reader} returned nothing to check"
    for entry in entries:
        missing = keys - set(entry)
        assert not missing, f"{reader} missing keys: {missing}"
    if reader == "get_latency_stats":
        for stats in entries:
            for pct in (stats["ttfb_percentiles"], stats["latency_percentiles"]):
                assert set(pct) >= {"p50", "p95", "p99"}


# ---------------------------------------------------------------------------
# Values, tenancy, windows, ordering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tenant", "until", "expected"),
    [
        ("acme", None, 0.06),  # 0.01 + 0.02 + 0.03, beta/default excluded
        ("beta", None, 0.05),
        ("", None, 0.007),  # default tenant is the empty string
        ("acme", float(_DAY0 + 1), 0.01),  # until= keeps only req-acme-1
    ],
    # _DAY0 comes from the clock, so a value-derived id would differ between
    # pytest-xdist workers collecting a second apart.
    ids=["acme", "beta", "default-tenant", "acme-until"],
)
async def test_cost_summary_total(seeded_client, tenant, until, expected):
    result = await rr.get_cost_summary(
        seeded_client, tenant=tenant, since=float(_DAY0 - 1), until=until
    )
    assert result["total"] == pytest.approx(expected, abs=1e-9)


async def test_cost_summary_scoped_and_project_rollup(seeded_client):
    result = await rr.get_cost_summary(seeded_client, **_ACME)
    assert "anthropic" not in result["by_provider"]
    assert "anthropic/claude-3-haiku" not in result["by_model"]
    # acme's seeded rows all carry project "default" (0.01 + 0.02 + 0.03).
    assert result["by_project"]["default"]["requests"] == 3
    assert result["by_project"]["default"]["cost"] == pytest.approx(0.06)


async def test_cost_by_day(seeded_client):
    result = await rr.get_cost_by_day(seeded_client, **_ACME)
    # acme has rows on _DAY0 and _DAY1 (two calendar days, 24h apart)
    assert len(result) == 2
    days = [entry["day"] for entry in result]
    assert days == sorted(days), f"Days not ascending: {days}"
    # beta 0.05 must not bleed in
    assert sum(e["cost"] for e in result) == pytest.approx(0.06, abs=1e-9)


class TestGetLatencyStats:
    async def test_values_and_tenant_scope(self, seeded_client):
        result = await rr.get_latency_stats(seeded_client, **_ACME)
        assert "anthropic/claude-3-haiku" not in result
        stats = result["openai/gpt-4o-mini"]
        assert stats["avg_ttfb_ms"] == pytest.approx(100.0, abs=1.0)
        assert stats["avg_latency_ms"] == pytest.approx(200.0, abs=1.0)
        assert stats["request_count"] == 1
        # req-acme-3 (deepgram/nova-2) has NULL latency: no samples, no percentiles
        if "deepgram/nova-2" in result:
            for pct_val in result["deepgram/nova-2"]["ttfb_percentiles"].values():
                assert pct_val is None, f"Expected None for null latency, got {pct_val}"

    async def test_cancelled_rows_are_excluded(self, ch_session_reads):
        """#280: cancelled work is not latency the caller experienced.

        The Latency page picks this reader or the SQL one at runtime on whether
        a ClickHouse client is bound, so a predicate on one side only makes the
        same page answer two different numbers depending on the backend. The
        cancelled row is 9000ms against real rows at 100-300ms. It gets its own
        seed so the exact-count tests elsewhere keep their numbers.
        """
        cancelled = _row(
            "req-acme-cancelled",
            cost_usd=0.0,
            status="cancelled",
            ttfb_ms=9000.0,
            total_latency_ms=9000.0,
            session_id="sess-acme-a",
        )
        _insert(ch_session_reads, [*_SEED_ROWS, cancelled])
        result = await rr.get_latency_stats(ChdbAdapter(ch_session_reads), **_ACME)
        entry = result["openai/gpt-4o-mini"]
        assert entry["avg_ttfb_ms"] < 1000.0, (
            f"cancelled row counted: avg_ttfb_ms={entry['avg_ttfb_ms']}"
        )
        assert entry["request_count"] == 1


@pytest.mark.parametrize("reader", ["get_recent_requests", "get_latency_stats"])
async def test_project_filter_excludes_other_projects(ch_session_reads, reader):
    """project= must exclude rows from other projects within the same tenant."""
    _insert(
        ch_session_reads,
        [
            _row("proj-default", project="default"),
            _row(
                "proj-other",
                project="other",
                provider="deepgram",
                model_id="deepgram/nova-2",
                cost_usd=0.02,
                ttfb_ms=999.0,
                total_latency_ms=9999.0,
            ),
        ],
    )
    result = await getattr(rr, reader)(
        ChdbAdapter(ch_session_reads), **_ACME, project="default"
    )
    if reader == "get_recent_requests":
        assert {r["id"] for r in result} == {"proj-default"}
    else:
        assert set(result) == {"openai/gpt-4o-mini"}


class TestGetRecentRequests:
    async def test_values_scope_and_order(self, seeded_client):
        result = await rr.get_recent_requests(seeded_client, **_ACME)
        assert len(result) == 3
        assert {r["id"] for r in result} == {"req-acme-1", "req-acme-2", "req-acme-3"}
        for row in result:
            assert isinstance(row["timestamp"], float)
        ts_list = [r["timestamp"] for r in result]
        assert ts_list == sorted(ts_list, reverse=True), f"not newest-first: {ts_list}"
        row = next(r for r in result if r["id"] == "req-acme-1")
        assert row["metadata"] == {"k": "v"}


class TestListSessions:
    async def test_values_scope_and_order(self, seeded_client):
        result = await rr.list_sessions(seeded_client, tenant="acme")
        assert {r["id"] for r in result} == {"sess-acme-a", "sess-acme-b"}
        assert all(r["tenant_id"] == "acme" for r in result)
        sess_a = next(r for r in result if r["id"] == "sess-acme-a")
        # sess-acme-a has 2 requests: 0.01 + 0.02 = 0.03
        assert sess_a["total_cost_usd"] == pytest.approx(0.03, abs=1e-9)
        assert sess_a["request_count"] == 2
        started = [r["started_at"] for r in result]
        assert started == sorted(started, reverse=True), f"not DESC: {started}"

    async def test_limit_respected(self, seeded_client):
        result = await rr.list_sessions(seeded_client, tenant="acme", limit=1)
        assert len(result) == 1  # acme has two sessions


async def test_cost_by_tenant_admin_spans_all_tenants(seeded_client):
    result = await rr.get_cost_by_tenant_admin(seeded_client, **_WIN)
    assert set(result) >= {"acme", "beta", ""}
    assert result["acme"]["cost"] == pytest.approx(0.06, abs=1e-9)
    assert result["acme"]["requests"] == 3
    assert result["beta"]["cost"] == pytest.approx(0.05, abs=1e-9)
    assert result["beta"]["requests"] == 1
    assert result[""]["cost"] == pytest.approx(0.007, abs=1e-9)


async def test_session_requests_scoped_ordered_and_reads_channel(ch_session_reads):
    tel = {"channel": "telephony"}
    _insert(
        ch_session_reads,
        [
            _row(
                "r1",
                modality="stt",
                provider="deepgram",
                model_id="deepgram/nova-3",
                session_id="call_a",
                metadata=tel,
            ),
            _row("r2", ts=_DAY1 + 2, session_id="call_a", metadata=tel),
            _row("r3", modality="tts", session_id="call_b"),
            _row("b1", tenant_id="beta", session_id="call_a"),
        ],
    )
    rows = await rr.get_session_requests(
        ChdbAdapter(ch_session_reads), tenant="acme", session_id="call_a"
    )
    # Only acme's call_a, oldest first (the call timeline).
    assert [r["id"] for r in rows] == ["r1", "r2"]
    assert rows[0]["modality"] == "stt"
    # Channel rides in metadata (no dedicated column).
    assert rows[0]["metadata"]["channel"] == "telephony"
    # beta's identically-named session never leaks into acme's drill-down.
    assert all(r["tenant_id"] == "acme" for r in rows)


# ---------------------------------------------------------------------------
# Billing reads (rated revenue + margin) over ClickHouse
# ---------------------------------------------------------------------------


def _insert_rated(sess, rows: list[dict]) -> None:
    """Insert rows carrying rated_price_usd + rate_rule (billing columns)."""
    for row in rows:
        sess.query(
            f"""
            INSERT INTO telemetry.requests
              (tenant_id, id, timestamp, modality, provider, model_id,
               input_units, output_units, cost_usd, rated_price_usd, rate_rule)
            VALUES
              ('{row["tenant_id"]}', '{row["id"]}', '{_ts(row.get("ts", _DAY1))}',
               '{row["modality"]}', '{row["provider"]}', '{row["model_id"]}',
               {row.get("input_units", 100)}, {row.get("output_units", 50)},
               {row["cost_usd"]}, {row["rated_price_usd"]}, '{row["rate_rule"]}')
            """,
            "CSV",
        )


_ACME_RATED = {
    "tenant_id": "acme",
    "modality": "stt",
    "provider": "deepgram",
    "model_id": "deepgram/nova-3",
    "cost_usd": 0.01,
    "rated_price_usd": 0.015,
    "rate_rule": "cost_plus:1.5",
}
_BILLING_ROWS = [
    # acme: 2 stt rows, cost 0.01 each, rated 0.015 each
    {**_ACME_RATED, "id": "b-acme-1"},
    {**_ACME_RATED, "id": "b-acme-2"},
    # beta: 1 llm row, cost 0.02, rated 0.021 (thin margin)
    {
        "tenant_id": "beta",
        "id": "b-beta-1",
        "modality": "llm",
        "provider": "openai",
        "model_id": "openai/gpt-4o",
        "cost_usd": 0.02,
        "rated_price_usd": 0.021,
        "rate_rule": "cost_plus:1.05",
    },
]


@pytest.fixture
def billing_client(ch_session_reads):
    _insert_rated(ch_session_reads, _BILLING_ROWS)
    return ChdbAdapter(ch_session_reads)


class TestBillableUsageCH:
    """get_billable_usage / get_tenant_line_items against chDB."""

    async def test_usage_rolls_up_per_tenant(self, billing_client):
        rows = await rr.get_billable_usage(billing_client, since=0.0, until=None)
        by_tenant = {r["tenant_id"]: r for r in rows}
        assert set(by_tenant) == {"acme", "beta"}
        acme = by_tenant["acme"]
        assert acme["requests"] == 2
        assert acme["cost_usd"] == pytest.approx(0.02)
        assert acme["rated_usd"] == pytest.approx(0.03)
        assert acme["margin_usd"] == pytest.approx(0.01)
        assert acme["margin_pct"] == pytest.approx(100.0 / 3.0)
        # Ordered by rated revenue descending.
        assert rows[0]["tenant_id"] == "acme"

    async def test_usage_filters_by_tenant(self, billing_client):
        rows = await rr.get_billable_usage(
            billing_client, since=0.0, until=None, tenant="beta"
        )
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "beta"
        assert rows[0]["rated_usd"] == pytest.approx(0.021)

    async def test_line_items_break_down_by_model(self, billing_client):
        items = await rr.get_tenant_line_items(
            billing_client, tenant="acme", since=0.0, until=None
        )
        assert len(items) == 1
        item = items[0]
        assert item["model_id"] == "deepgram/nova-3"
        assert item["requests"] == 2
        assert item["rated_usd"] == pytest.approx(0.03)
        assert item["margin_usd"] == pytest.approx(0.01)


async def test_billing_usage_endpoint_reads_from_clickhouse(
    billing_client, tmp_path, monkeypatch
):
    """GET /v1/billing/usage reads from ClickHouse when ch_client is set."""
    import yaml
    from httpx import ASGITransport, AsyncClient

    from voicegateway.core.gateway import Gateway
    from voicegateway.server import build_app

    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "ep.db"))
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
    cfg = {
        "cost_tracking": {"enabled": True},
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
    }
    p = tmp_path / "voicegw.yaml"
    p.write_text(yaml.dump(cfg))
    app = build_app(
        Gateway(config_path=str(p)), enable_mcp_sse=False, enable_dashboard=False
    )
    # ASGITransport does not run lifespan, so this manual client persists.
    app.state.ch_client = billing_client

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.get("/v1/billing/usage?period=month")

    assert resp.status_code == 200
    body = resp.json()
    by_tenant = {r["tenant_id"]: r for r in body["tenants"]}
    assert by_tenant["acme"]["rated_usd"] == pytest.approx(0.03)
    assert body["totals"]["rated_usd"] == pytest.approx(0.051)
