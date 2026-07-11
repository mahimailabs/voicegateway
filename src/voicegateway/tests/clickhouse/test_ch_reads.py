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
# chDB session fixture + query helper
# ---------------------------------------------------------------------------


@pytest.fixture
def ch_session_reads(tmp_path):
    """Fresh chDB session with all migrations applied."""
    sess = chdb_session.Session(str(tmp_path / "ch_reads"))
    from voicegateway.clickhouse.migrate import apply_migrations_to_session

    apply_migrations_to_session(sess, MIGRATIONS_DIR)
    yield sess
    sess.close()


def _chdb_query(sess, sql: str) -> str:
    result = sess.query(sql, "CSV")
    raw = result.bytes() if hasattr(result, "bytes") else bytes(result)
    return raw.decode().strip()


# ---------------------------------------------------------------------------
# chDB adapter (test file only -- NOT production code)
#
# The read repository functions take a clickhouse-connect AsyncClient with:
#   result = await client.query(sql, parameters={...})
#   result.result_rows  ->  list[tuple]
#
# chDB is sync and uses positional {name:Type} placeholders that it resolves
# inline. We translate by replacing every {name:Type} placeholder with the
# literal value (safe here because we control 100% of the test parameters
# and the production code uses server-side binding in production).
#
# To bridge async -> sync in tests, the adapter exposes a synchronous .query()
# that the test calls via asyncio (asyncio_mode="auto" means pytest handles it).
# ---------------------------------------------------------------------------


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
    """Sync chDB session wrapped in an async-compatible interface.

    Only .query() is implemented (sufficient for the read repository).
    Parameters are substituted via _apply_params (chDB does not support
    server-side bind params).
    """

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
        rows = [tuple(row) for row in parsed.get("data", [])]
        return _ChdbQueryResult(rows)


# ---------------------------------------------------------------------------
# Seed helper
# ---------------------------------------------------------------------------

# Epoch seconds anchors for seed data
_NOW = int(time.time())
_DAY0 = _NOW - 86400 * 2
_DAY1 = _NOW - 86400


def _ts(epoch: int) -> str:
    """Format epoch seconds as ClickHouse DateTime64 string."""
    from datetime import datetime

    dt = datetime.fromtimestamp(epoch, tz=UTC)
    return dt.strftime("%Y-%m-%d %H:%M:%S.000")


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
    # acme - session-a, day 0, openai
    {
        "tenant_id": "acme",
        "id": "req-acme-1",
        "ts": _DAY0,
        "modality": "llm",
        "provider": "openai",
        "model_id": "openai/gpt-4o-mini",
        "cost_usd": 0.01,
        "ttfb_ms": 100.0,
        "total_latency_ms": 200.0,
        "session_id": "sess-acme-a",
        "agent_id": "agent-1",
        "metadata": {"k": "v"},
    },
    # acme - session-a, day 1, openai
    {
        "tenant_id": "acme",
        "id": "req-acme-2",
        "ts": _DAY1,
        "modality": "stt",
        "provider": "openai",
        "model_id": "openai/whisper-1",
        "cost_usd": 0.02,
        "ttfb_ms": 150.0,
        "total_latency_ms": 300.0,
        "session_id": "sess-acme-a",
        "agent_id": "agent-1",
    },
    # acme - session-b, day 1, deepgram
    {
        "tenant_id": "acme",
        "id": "req-acme-3",
        "ts": _DAY1,
        "modality": "stt",
        "provider": "deepgram",
        "model_id": "deepgram/nova-2",
        "cost_usd": 0.03,
        "ttfb_ms": None,
        "total_latency_ms": None,
        "session_id": "sess-acme-b",
        "agent_id": "agent-2",
    },
    # beta - session-c
    {
        "tenant_id": "beta",
        "id": "req-beta-1",
        "ts": _DAY1,
        "modality": "llm",
        "provider": "anthropic",
        "model_id": "anthropic/claude-3-haiku",
        "cost_usd": 0.05,
        "ttfb_ms": 80.0,
        "total_latency_ms": 160.0,
        "session_id": "sess-beta-c",
        "agent_id": "agent-b",
    },
    # default tenant ('')
    {
        "tenant_id": "",
        "id": "req-default-1",
        "ts": _DAY1,
        "modality": "tts",
        "provider": "cartesia",
        "model_id": "cartesia/sonic",
        "cost_usd": 0.007,
        "ttfb_ms": 50.0,
        "total_latency_ms": 120.0,
        "session_id": "sess-default",
        "agent_id": "",
    },
]


@pytest.fixture
def seeded_session(ch_session_reads):
    """ch_session_reads with all seed rows inserted."""
    _insert(ch_session_reads, _SEED_ROWS)
    return ch_session_reads


@pytest.fixture
def seeded_client(seeded_session):
    """ChdbAdapter wrapping the seeded session."""
    return ChdbAdapter(seeded_session)


# ---------------------------------------------------------------------------
# Tests: get_cost_summary
# ---------------------------------------------------------------------------


class TestGetCostSummary:
    async def test_acme_total_excludes_beta_and_default(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_cost_summary

        result = await get_cost_summary(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        # acme costs: 0.01 + 0.02 + 0.03 = 0.06
        assert abs(result["total"] - 0.06) < 1e-9, f"total={result['total']}"

    async def test_acme_total_never_returns_beta_rows(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_cost_summary

        result = await get_cost_summary(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        # beta has anthropic provider - must not appear
        assert "anthropic" not in result["by_provider"]
        # beta model must not appear
        assert "anthropic/claude-3-haiku" not in result["by_model"]

    async def test_dict_shape(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_cost_summary

        result = await get_cost_summary(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
            project="default",
        )
        assert "period" in result
        assert "project" in result
        assert "total" in result
        assert "by_provider" in result
        assert "by_model" in result
        assert "by_project" in result
        # period carries since/until range marker
        assert result["project"] == "default"

    async def test_by_provider_shape(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_cost_summary

        result = await get_cost_summary(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        for _prov, stats in result["by_provider"].items():
            assert "cost" in stats
            assert "requests" in stats

    async def test_by_model_shape(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_cost_summary

        result = await get_cost_summary(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        for _model, stats in result["by_model"].items():
            assert "cost" in stats
            assert "requests" in stats

    async def test_by_project_shape_and_rollup(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_cost_summary

        result = await get_cost_summary(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        for _project, stats in result["by_project"].items():
            assert "cost" in stats
            assert "requests" in stats
        # acme's seeded rows all carry project "default" (0.01 + 0.02 + 0.03).
        assert result["by_project"]["default"]["requests"] == 3
        assert result["by_project"]["default"]["cost"] == pytest.approx(0.06)

    async def test_beta_total(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_cost_summary

        result = await get_cost_summary(
            seeded_client,
            tenant="beta",
            since=float(_DAY0 - 1),
            until=None,
        )
        assert abs(result["total"] - 0.05) < 1e-9

    async def test_default_tenant_empty_string(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_cost_summary

        result = await get_cost_summary(
            seeded_client,
            tenant="",
            since=float(_DAY0 - 1),
            until=None,
        )
        assert abs(result["total"] - 0.007) < 1e-9

    async def test_until_filter_excludes_older_rows(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_cost_summary

        # until=_DAY0+1 means only req-acme-1 qualifies
        result = await get_cost_summary(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=float(_DAY0 + 1),
        )
        assert abs(result["total"] - 0.01) < 1e-9


# ---------------------------------------------------------------------------
# Tests: get_cost_by_day
# ---------------------------------------------------------------------------


class TestGetCostByDay:
    async def test_returns_list(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_cost_by_day

        result = await get_cost_by_day(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        assert isinstance(result, list)

    async def test_each_entry_has_day_cost_requests(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_cost_by_day

        result = await get_cost_by_day(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        for entry in result:
            assert "day" in entry
            assert "cost" in entry
            assert "requests" in entry

    async def test_acme_spans_two_days(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_cost_by_day

        result = await get_cost_by_day(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        # acme has rows on _DAY0 and _DAY1 (two different calendar days, 24h apart)
        assert len(result) == 2

    async def test_tenant_scoped_excludes_beta(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_cost_by_day

        result = await get_cost_by_day(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        total_cost = sum(entry["cost"] for entry in result)
        # acme total: 0.01 + 0.02 + 0.03 = 0.06; beta 0.05 must not bleed in
        assert abs(total_cost - 0.06) < 1e-9

    async def test_ordered_ascending(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_cost_by_day

        result = await get_cost_by_day(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        days = [entry["day"] for entry in result]
        assert days == sorted(days), f"Days not ascending: {days}"


# ---------------------------------------------------------------------------
# Tests: get_latency_stats
# ---------------------------------------------------------------------------


class TestGetLatencyStats:
    async def test_returns_dict_keyed_by_model(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_latency_stats

        result = await get_latency_stats(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        assert isinstance(result, dict)

    async def test_model_entry_has_required_keys(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_latency_stats

        result = await get_latency_stats(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        for _model, stats in result.items():
            assert "avg_ttfb_ms" in stats
            assert "avg_latency_ms" in stats
            assert "request_count" in stats
            assert "ttfb_percentiles" in stats
            assert "latency_percentiles" in stats

    async def test_percentile_keys_p50_p95_p99(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_latency_stats

        result = await get_latency_stats(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        for _model, stats in result.items():
            for pct_dict in (stats["ttfb_percentiles"], stats["latency_percentiles"]):
                assert "p50" in pct_dict
                assert "p95" in pct_dict
                assert "p99" in pct_dict

    async def test_model_with_null_latency_gets_none_percentiles(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_latency_stats

        # req-acme-3 (deepgram/nova-2) has NULL ttfb_ms and total_latency_ms
        result = await get_latency_stats(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        if "deepgram/nova-2" in result:
            stats = result["deepgram/nova-2"]
            # percentiles should be None when no samples exist
            for pct_val in stats["ttfb_percentiles"].values():
                assert pct_val is None, f"Expected None for null latency, got {pct_val}"

    async def test_tenant_scoped_no_beta_models(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_latency_stats

        result = await get_latency_stats(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        assert "anthropic/claude-3-haiku" not in result

    async def test_avg_values_for_gpt4o_mini(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_latency_stats

        result = await get_latency_stats(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        assert "openai/gpt-4o-mini" in result
        stats = result["openai/gpt-4o-mini"]
        assert abs(stats["avg_ttfb_ms"] - 100.0) < 1.0
        assert abs(stats["avg_latency_ms"] - 200.0) < 1.0
        assert stats["request_count"] == 1


# ---------------------------------------------------------------------------
# Tests: get_recent_requests
# ---------------------------------------------------------------------------


class TestGetRecentRequests:
    async def test_returns_list_of_dicts(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_recent_requests

        result = await get_recent_requests(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        assert isinstance(result, list)
        assert len(result) == 3  # 3 acme rows

    async def test_required_keys_present(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_recent_requests

        result = await get_recent_requests(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        required = {
            "id",
            "timestamp",
            "project",
            "modality",
            "model_id",
            "provider",
            "input_units",
            "output_units",
            "cached_input_units",
            "cost_usd",
            "pricing_source",
            "ttfb_ms",
            "total_latency_ms",
            "status",
            "fallback_from",
            "error_message",
            "metadata",
            "session_id",
            "tenant_id",
            "agent_id",
        }
        for row in result:
            missing = required - set(row.keys())
            assert not missing, f"Missing keys: {missing}"

    async def test_timestamp_is_float(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_recent_requests

        result = await get_recent_requests(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        for row in result:
            assert isinstance(row["timestamp"], float), (
                f"timestamp should be float, got {type(row['timestamp'])}"
            )

    async def test_metadata_is_dict(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_recent_requests

        result = await get_recent_requests(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        # req-acme-1 has metadata {"k": "v"}
        row = next(r for r in result if r["id"] == "req-acme-1")
        assert isinstance(row["metadata"], dict), (
            f"metadata should be dict, got {type(row['metadata'])}"
        )
        assert row["metadata"].get("k") == "v"

    async def test_ordered_newest_first(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_recent_requests

        result = await get_recent_requests(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        ts_list = [r["timestamp"] for r in result]
        assert ts_list == sorted(ts_list, reverse=True), (
            f"Expected newest-first, got {ts_list}"
        )

    async def test_tenant_scoped_no_beta_rows(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_recent_requests

        result = await get_recent_requests(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
        )
        ids = {r["id"] for r in result}
        assert "req-beta-1" not in ids
        assert "req-default-1" not in ids

    async def test_limit_respected(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_recent_requests

        result = await get_recent_requests(
            seeded_client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
            limit=2,
        )
        assert len(result) <= 2

    async def test_project_filter_excludes_other_projects(self, ch_session_reads):
        """project= must exclude rows from other projects within the same tenant.

        Seeds two acme rows: one in project='default', one in project='other'.
        Querying project='default' must return exactly 1 row (the non-matching
        project row must not leak through).
        """
        from voicegateway.clickhouse.read_repository import get_recent_requests

        _insert(
            ch_session_reads,
            [
                {
                    "tenant_id": "acme",
                    "id": "rr-proj-default",
                    "ts": _DAY1,
                    "project": "default",
                    "modality": "llm",
                    "provider": "openai",
                    "model_id": "openai/gpt-4o-mini",
                    "cost_usd": 0.01,
                    "ttfb_ms": 100.0,
                    "total_latency_ms": 200.0,
                    "session_id": "sess-proj-test",
                    "agent_id": "agent-1",
                },
                {
                    "tenant_id": "acme",
                    "id": "rr-proj-other",
                    "ts": _DAY1,
                    "project": "other",
                    "modality": "llm",
                    "provider": "openai",
                    "model_id": "openai/gpt-4o-mini",
                    "cost_usd": 0.02,
                    "ttfb_ms": 110.0,
                    "total_latency_ms": 210.0,
                    "session_id": "sess-proj-test",
                    "agent_id": "agent-1",
                },
            ],
        )
        client = ChdbAdapter(ch_session_reads)
        result = await get_recent_requests(
            client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
            project="default",
        )
        ids = {r["id"] for r in result}
        assert "rr-proj-default" in ids, "Row for project='default' must appear"
        assert "rr-proj-other" not in ids, (
            "Row for project='other' must be excluded when project='default' is filtered"
        )


# ---------------------------------------------------------------------------
# Tests: get_latency_stats project filter
# ---------------------------------------------------------------------------


class TestGetLatencyStatsProjectFilter:
    async def test_project_filter_excludes_other_projects(self, ch_session_reads):
        """project= must exclude rows from other projects within the same tenant.

        Seeds two acme rows: one in project='default', one in project='other'.
        Querying project='default' must return stats only for that project
        (the non-matching project row must not contribute to aggregates).
        """
        from voicegateway.clickhouse.read_repository import get_latency_stats

        _insert(
            ch_session_reads,
            [
                {
                    "tenant_id": "acme",
                    "id": "lat-proj-default",
                    "ts": _DAY1,
                    "project": "default",
                    "modality": "llm",
                    "provider": "openai",
                    "model_id": "openai/gpt-4o-mini",
                    "cost_usd": 0.01,
                    "ttfb_ms": 100.0,
                    "total_latency_ms": 200.0,
                    "session_id": "sess-lat-test",
                    "agent_id": "agent-1",
                },
                {
                    "tenant_id": "acme",
                    "id": "lat-proj-other",
                    "ts": _DAY1,
                    "project": "other",
                    "modality": "llm",
                    "provider": "deepgram",
                    "model_id": "deepgram/nova-2",
                    "cost_usd": 0.02,
                    "ttfb_ms": 999.0,
                    "total_latency_ms": 9999.0,
                    "session_id": "sess-lat-test",
                    "agent_id": "agent-1",
                },
            ],
        )
        client = ChdbAdapter(ch_session_reads)
        result = await get_latency_stats(
            client,
            tenant="acme",
            since=float(_DAY0 - 1),
            until=None,
            project="default",
        )
        assert "deepgram/nova-2" not in result, (
            "deepgram/nova-2 is in project='other' and must be excluded "
            "when filtering project='default'"
        )
        assert "openai/gpt-4o-mini" in result, (
            "openai/gpt-4o-mini is in project='default' and must appear"
        )


# ---------------------------------------------------------------------------
# Tests: list_sessions
# ---------------------------------------------------------------------------


class TestListSessions:
    async def test_returns_list(self, seeded_client):
        from voicegateway.clickhouse.read_repository import list_sessions

        result = await list_sessions(
            seeded_client,
            tenant="acme",
        )
        assert isinstance(result, list)

    async def test_acme_has_two_sessions(self, seeded_client):
        from voicegateway.clickhouse.read_repository import list_sessions

        result = await list_sessions(
            seeded_client,
            tenant="acme",
        )
        session_ids = {r["id"] for r in result}
        assert "sess-acme-a" in session_ids
        assert "sess-acme-b" in session_ids

    async def test_session_dict_shape(self, seeded_client):
        from voicegateway.clickhouse.read_repository import list_sessions

        result = await list_sessions(
            seeded_client,
            tenant="acme",
        )
        required = {
            "id",
            "started_at",
            "ended_at",
            "total_cost_usd",
            "request_count",
            "tenant_id",
            "agent_id",
        }
        for row in result:
            missing = required - set(row.keys())
            assert not missing, f"Missing session keys: {missing}"

    async def test_tenant_scoped_no_beta_sessions(self, seeded_client):
        from voicegateway.clickhouse.read_repository import list_sessions

        result = await list_sessions(
            seeded_client,
            tenant="acme",
        )
        ids = {r["id"] for r in result}
        assert "sess-beta-c" not in ids
        assert "sess-default" not in ids

    async def test_session_a_cost_and_count(self, seeded_client):
        from voicegateway.clickhouse.read_repository import list_sessions

        result = await list_sessions(
            seeded_client,
            tenant="acme",
        )
        sess_a = next((r for r in result if r["id"] == "sess-acme-a"), None)
        assert sess_a is not None
        # sess-acme-a has 2 requests: 0.01 + 0.02 = 0.03
        assert abs(sess_a["total_cost_usd"] - 0.03) < 1e-9
        assert sess_a["request_count"] == 2

    async def test_ordered_by_started_at_desc(self, seeded_client):
        from voicegateway.clickhouse.read_repository import list_sessions

        result = await list_sessions(
            seeded_client,
            tenant="acme",
        )
        started = [r["started_at"] for r in result]
        assert started == sorted(started, reverse=True), (
            f"Expected started_at DESC, got {started}"
        )

    async def test_limit_respected(self, seeded_client):
        from voicegateway.clickhouse.read_repository import list_sessions

        result = await list_sessions(
            seeded_client,
            tenant="acme",
            limit=1,
        )
        assert len(result) <= 1

    async def test_tenant_id_field_matches(self, seeded_client):
        from voicegateway.clickhouse.read_repository import list_sessions

        result = await list_sessions(
            seeded_client,
            tenant="acme",
        )
        for row in result:
            assert row["tenant_id"] == "acme"


# ---------------------------------------------------------------------------
# Tests: get_cost_by_tenant_admin
# ---------------------------------------------------------------------------


class TestGetCostByTenantAdmin:
    async def test_returns_all_three_tenants(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_cost_by_tenant_admin

        result = await get_cost_by_tenant_admin(
            seeded_client,
            since=float(_DAY0 - 1),
            until=None,
        )
        assert isinstance(result, dict)
        assert "acme" in result
        assert "beta" in result
        assert "" in result

    async def test_acme_cost_correct(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_cost_by_tenant_admin

        result = await get_cost_by_tenant_admin(
            seeded_client,
            since=float(_DAY0 - 1),
            until=None,
        )
        assert abs(result["acme"]["cost"] - 0.06) < 1e-9
        assert result["acme"]["requests"] == 3

    async def test_beta_cost_correct(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_cost_by_tenant_admin

        result = await get_cost_by_tenant_admin(
            seeded_client,
            since=float(_DAY0 - 1),
            until=None,
        )
        assert abs(result["beta"]["cost"] - 0.05) < 1e-9
        assert result["beta"]["requests"] == 1

    async def test_default_tenant_cost_correct(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_cost_by_tenant_admin

        result = await get_cost_by_tenant_admin(
            seeded_client,
            since=float(_DAY0 - 1),
            until=None,
        )
        assert abs(result[""]["cost"] - 0.007) < 1e-9

    async def test_each_entry_shape(self, seeded_client):
        from voicegateway.clickhouse.read_repository import get_cost_by_tenant_admin

        result = await get_cost_by_tenant_admin(
            seeded_client,
            since=float(_DAY0 - 1),
            until=None,
        )
        for _tenant, stats in result.items():
            assert "cost" in stats
            assert "requests" in stats


class TestGetSessionRequests:
    async def test_scoped_ordered_and_reads_channel(self, ch_session_reads):
        from voicegateway.clickhouse.read_repository import get_session_requests

        _insert(
            ch_session_reads,
            [
                {
                    "tenant_id": "acme",
                    "id": "r1",
                    "ts": _DAY1,
                    "modality": "stt",
                    "provider": "deepgram",
                    "model_id": "deepgram/nova-3",
                    "cost_usd": 0.001,
                    "ttfb_ms": 110.0,
                    "total_latency_ms": 110.0,
                    "session_id": "call_a",
                    "agent_id": "w1",
                    "metadata": {"channel": "telephony"},
                },
                {
                    "tenant_id": "acme",
                    "id": "r2",
                    "ts": _DAY1 + 2,
                    "modality": "llm",
                    "provider": "openai",
                    "model_id": "openai/gpt-4.1-mini",
                    "cost_usd": 0.004,
                    "ttfb_ms": 200.0,
                    "total_latency_ms": 200.0,
                    "session_id": "call_a",
                    "agent_id": "w1",
                    "metadata": {"channel": "telephony"},
                },
                {
                    "tenant_id": "acme",
                    "id": "r3",
                    "ts": _DAY1,
                    "modality": "tts",
                    "provider": "deepgram",
                    "model_id": "deepgram/aura-2",
                    "cost_usd": 0.002,
                    "ttfb_ms": 135.0,
                    "total_latency_ms": 135.0,
                    "session_id": "call_b",
                    "agent_id": "w1",
                    "metadata": {},
                },
                {
                    "tenant_id": "beta",
                    "id": "b1",
                    "ts": _DAY1,
                    "modality": "llm",
                    "provider": "openai",
                    "model_id": "m",
                    "cost_usd": 0.004,
                    "ttfb_ms": 90.0,
                    "total_latency_ms": 90.0,
                    "session_id": "call_a",
                    "agent_id": "w2",
                    "metadata": {},
                },
            ],
        )
        client = ChdbAdapter(ch_session_reads)
        rows = await get_session_requests(client, tenant="acme", session_id="call_a")

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


_BILLING_ROWS = [
    # acme: 2 stt rows, cost 0.01 each, rated 0.015 each
    {
        "tenant_id": "acme",
        "id": "b-acme-1",
        "modality": "stt",
        "provider": "deepgram",
        "model_id": "deepgram/nova-3",
        "cost_usd": 0.01,
        "rated_price_usd": 0.015,
        "rate_rule": "cost_plus:1.5",
    },
    {
        "tenant_id": "acme",
        "id": "b-acme-2",
        "modality": "stt",
        "provider": "deepgram",
        "model_id": "deepgram/nova-3",
        "cost_usd": 0.01,
        "rated_price_usd": 0.015,
        "rate_rule": "cost_plus:1.5",
    },
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


@pytest.mark.skipif(not CHDB_AVAILABLE, reason="chdb not installed")
class TestBillableUsageCH:
    """get_billable_usage / get_tenant_line_items against chDB."""

    async def test_usage_rolls_up_per_tenant(self, ch_session_reads):
        from voicegateway.clickhouse.read_repository import get_billable_usage

        _insert_rated(ch_session_reads, _BILLING_ROWS)
        client = ChdbAdapter(ch_session_reads)
        rows = await get_billable_usage(client, since=0.0, until=None)

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

    async def test_usage_filters_by_tenant(self, ch_session_reads):
        from voicegateway.clickhouse.read_repository import get_billable_usage

        _insert_rated(ch_session_reads, _BILLING_ROWS)
        client = ChdbAdapter(ch_session_reads)
        rows = await get_billable_usage(client, since=0.0, until=None, tenant="beta")
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "beta"
        assert rows[0]["rated_usd"] == pytest.approx(0.021)

    async def test_line_items_break_down_by_model(self, ch_session_reads):
        from voicegateway.clickhouse.read_repository import get_tenant_line_items

        _insert_rated(ch_session_reads, _BILLING_ROWS)
        client = ChdbAdapter(ch_session_reads)
        items = await get_tenant_line_items(
            client, tenant="acme", since=0.0, until=None
        )
        assert len(items) == 1
        item = items[0]
        assert item["model_id"] == "deepgram/nova-3"
        assert item["requests"] == 2
        assert item["rated_usd"] == pytest.approx(0.03)
        assert item["margin_usd"] == pytest.approx(0.01)


@pytest.mark.skipif(not CHDB_AVAILABLE, reason="chdb not installed")
class TestBillingUsageEndpointCH:
    """GET /v1/billing/usage reads from ClickHouse when ch_client is set."""

    async def test_endpoint_reads_from_clickhouse(
        self, ch_session_reads, tmp_path, monkeypatch
    ):
        import yaml
        from httpx import ASGITransport, AsyncClient

        from voicegateway.core.gateway import Gateway
        from voicegateway.server import build_app

        _insert_rated(ch_session_reads, _BILLING_ROWS)
        monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "ep.db"))
        monkeypatch.delenv("VOICEGW_API_KEY", raising=False)
        cfg = {
            "cost_tracking": {"enabled": True},
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
        }
        p = tmp_path / "voicegw.yaml"
        p.write_text(yaml.dump(cfg))
        gw = Gateway(config_path=str(p))
        app = build_app(gw, enable_mcp_sse=False, enable_dashboard=False)
        # ASGITransport does not run lifespan, so this manual client persists.
        app.state.ch_client = ChdbAdapter(ch_session_reads)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.get("/v1/billing/usage?period=month")

        assert resp.status_code == 200
        body = resp.json()
        by_tenant = {r["tenant_id"]: r for r in body["tenants"]}
        assert by_tenant["acme"]["rated_usd"] == pytest.approx(0.03)
        assert body["totals"]["rated_usd"] == pytest.approx(0.051)
