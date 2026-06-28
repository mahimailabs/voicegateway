"""Integration tests: ClickHouse schema + MV via testcontainers.

These tests start a real ClickHouse container (image 26.1) and verify:
- Migration runner applies all DDL against a live server
- Inserting into telemetry.requests with a session_id populates sessions_agg
  via the materialized view (the path chDB cannot faithfully emulate)

Run with:
    pytest -m integration src/voicegateway/tests/clickhouse/test_ch_schema_integration.py

Docker must be running and the clickhouse/clickhouse-server:26.1 image must be
accessible (first run will pull ~1 GB).
"""

from __future__ import annotations

import pathlib
import time
from datetime import UTC, datetime

import pytest

MIGRATIONS_DIR = (
    pathlib.Path(__file__).parent.parent.parent / "clickhouse" / "migrations"
)

# ---------------------------------------------------------------------------
# Container fixture (module-scoped: one container for all tests in this file)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def clickhouse_container():
    """Start a ClickHouse 26.1 container for the duration of this module."""
    from testcontainers.clickhouse import ClickHouseContainer

    # testcontainers ClickHouseContainer defaults: username=test, password=test
    with ClickHouseContainer(image="clickhouse/clickhouse-server:26.1") as container:
        yield container


@pytest.fixture(scope="module")
def ch_client(clickhouse_container):
    """Sync clickhouse-connect client connected to the test container."""
    import clickhouse_connect

    host = clickhouse_container.get_container_host_ip()
    port = int(clickhouse_container.get_exposed_port(8123))
    client = clickhouse_connect.get_client(
        host=host,
        port=port,
        username=clickhouse_container.username,
        password=clickhouse_container.password,
        database=clickhouse_container.dbname,
    )
    yield client
    client.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestIntegrationMigrations:
    def test_migration_runner_applies_all_ddl(self, ch_client):
        """Apply migrations via the sync-session shim using a real ClickHouse."""
        from voicegateway.clickhouse.migrate import _migration_files, _split_statements

        # Bootstrap: create the telemetry DB and schema_migrations table directly
        ch_client.command("CREATE DATABASE IF NOT EXISTS telemetry")
        ch_client.command(
            """
            CREATE TABLE IF NOT EXISTS telemetry.schema_migrations (
                version     UInt32,
                name        String,
                applied_at  DateTime DEFAULT now()
            )
            ENGINE = MergeTree
            ORDER BY version
            """
        )

        # Apply each migration file
        for version, name, path in _migration_files(MIGRATIONS_DIR):
            sql = path.read_text()
            for stmt in _split_statements(sql):
                ch_client.command(stmt)
            ch_client.command(
                f"INSERT INTO telemetry.schema_migrations (version, name) "
                f"VALUES ({version}, '{name}')"
            )

        # Verify all expected tables exist
        result = ch_client.query(
            "SELECT name FROM system.tables WHERE database='telemetry' ORDER BY name"
        )
        names = {row[0] for row in result.result_rows}
        assert "requests" in names, f"requests not found in: {names}"
        assert "sessions_agg" in names, f"sessions_agg not found in: {names}"
        assert "sessions_mv" in names, f"sessions_mv not found in: {names}"
        assert "schema_migrations" in names, f"schema_migrations not found in: {names}"

    def test_schema_migrations_versions_recorded(self, ch_client):
        result = ch_client.query(
            "SELECT version FROM telemetry.schema_migrations ORDER BY version"
        )
        versions = {row[0] for row in result.result_rows}
        assert 1 in versions
        assert 2 in versions

    def test_requests_order_by_leads_with_tenant_id(self, ch_client):
        result = ch_client.query(
            "SELECT sorting_key FROM system.tables "
            "WHERE database='telemetry' AND name='requests'"
        )
        sorting_key = result.result_rows[0][0]
        assert sorting_key.startswith("tenant_id"), (
            f"Expected sorting_key to start with tenant_id, got: {sorting_key!r}"
        )

    def test_requests_engine_is_replacing_merge_tree(self, ch_client):
        result = ch_client.query(
            "SELECT engine FROM system.tables "
            "WHERE database='telemetry' AND name='requests'"
        )
        engine = result.result_rows[0][0]
        assert "ReplacingMergeTree" in engine

    def test_mv_populates_sessions_agg_on_insert(self, ch_client):
        """Insert a request with a session_id; the MV should populate sessions_agg."""
        ts = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)

        ch_client.insert(
            "telemetry.requests",
            [
                [
                    "tenant-A",  # tenant_id
                    "req-integ-1",  # id
                    ts,  # timestamp
                    "proj-x",  # project
                    "llm",  # modality
                    "openai",  # provider
                    "gpt-4o",  # model_id
                    1000.0,  # input_units
                    500.0,  # output_units
                    0.0,  # cached_input_units
                    0.025,  # cost_usd
                    "voice-prices",  # pricing_source
                    None,  # ttfb_ms
                    None,  # total_latency_ms
                    "success",  # status
                    "",  # fallback_from
                    "",  # error_message
                    "sess-integ-1",  # session_id
                    "agent-1",  # agent_id
                    "{}",  # metadata
                ]
            ],
            column_names=[
                "tenant_id",
                "id",
                "timestamp",
                "project",
                "modality",
                "provider",
                "model_id",
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
                "session_id",
                "agent_id",
                "metadata",
            ],
        )

        # ClickHouse MV fires synchronously on INSERT but we give a small grace
        # period to account for any internal buffering on the test host.
        deadline = time.monotonic() + 5.0
        rows = []
        while time.monotonic() < deadline:
            result = ch_client.query(
                "SELECT session_id, total_cost_usd, request_count "
                "FROM telemetry.sessions_agg "
                "WHERE tenant_id='tenant-A' AND session_id='sess-integ-1'"
            )
            rows = result.result_rows
            if rows:
                break
            time.sleep(0.2)

        assert rows, "sessions_agg was not populated by the MV after insert"
        session_id, total_cost, req_count = rows[0]
        assert session_id == "sess-integ-1"
        assert total_cost == pytest.approx(0.025, rel=1e-3)
        assert req_count >= 1

    def test_idempotent_remigration(self, ch_client):
        """Applying the same migrations again via IF NOT EXISTS should not raise."""
        from voicegateway.clickhouse.migrate import _migration_files, _split_statements

        for _version, _name, path in _migration_files(MIGRATIONS_DIR):
            sql = path.read_text()
            for stmt in _split_statements(sql):
                ch_client.command(stmt)  # IF NOT EXISTS makes this a no-op
