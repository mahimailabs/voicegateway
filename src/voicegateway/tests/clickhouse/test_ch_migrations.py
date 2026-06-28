"""chDB unit tests for ClickHouse migration runner and schema correctness.

These tests run entirely in-process using chdb's persistent Session.
They verify:
- Migration runner creates `schema_migrations` tracking table
- All DDL files apply idempotently (IF NOT EXISTS)
- Table ORDER BY leads with tenant_id for requests
- sessions_agg and sessions_mv objects exist
- turns table exists
- schema_migrations records all applied versions
"""

from __future__ import annotations

import pathlib

import pytest

# chdb is an in-process ClickHouse engine; import before any test runs.
try:
    from chdb import session as chdb_session

    CHDB_AVAILABLE = True
except ImportError:
    CHDB_AVAILABLE = False

pytestmark = pytest.mark.skipif(not CHDB_AVAILABLE, reason="chdb not installed")

MIGRATIONS_DIR = (
    pathlib.Path(__file__).parent.parent.parent / "clickhouse" / "migrations"
)


@pytest.fixture
def ch_session(tmp_path):
    """A fresh chDB persistent session pointing at a temp dir."""
    sess = chdb_session.Session(str(tmp_path / "ch_test"))
    yield sess
    sess.close()


def _query(sess, sql: str) -> str:
    """Run a query and return the result as a stripped string."""
    result = sess.query(sql, "CSV")
    # chdb v4+ returns query_result with .bytes() method
    raw = result.bytes() if hasattr(result, "bytes") else bytes(result)
    return raw.decode().strip()


def apply_all(sess):
    """Apply all migrations via the runner using chdb session."""
    from voicegateway.clickhouse.migrate import apply_migrations_to_session

    apply_migrations_to_session(sess, MIGRATIONS_DIR)


class TestMigrationRunner:
    def test_schema_migrations_table_created(self, ch_session):
        apply_all(ch_session)
        result = _query(
            ch_session,
            "SELECT count() FROM telemetry.schema_migrations",
        )
        # Should have at least some rows (one per migration file)
        assert int(result) >= 1

    def test_applied_versions_recorded(self, ch_session):
        apply_all(ch_session)
        result = _query(
            ch_session,
            "SELECT version FROM telemetry.schema_migrations ORDER BY version",
        )
        versions = [int(v) for v in result.splitlines() if v]
        assert 1 in versions
        assert 2 in versions
        assert 3 in versions

    def test_idempotent_double_apply(self, ch_session):
        apply_all(ch_session)
        # Should not raise; IF NOT EXISTS guards make this safe
        apply_all(ch_session)
        result = _query(
            ch_session,
            "SELECT count() FROM telemetry.schema_migrations",
        )
        # Exactly one row per migration file after double apply
        expected = len(list(MIGRATIONS_DIR.glob("*.sql")))
        assert int(result) == expected

    def test_bad_filename_rejected_by_regex(self, tmp_path):
        """Migration filenames with unsafe characters must be silently skipped."""
        import shutil

        from voicegateway.clickhouse.migrate import _migration_files

        # Copy real migrations into a temp dir so we can add a bad one
        bad_dir = tmp_path / "bad_migrations"
        shutil.copytree(str(MIGRATIONS_DIR), str(bad_dir))

        # Write a file whose name component contains a quote (SQL injection attempt)
        bad_file = bad_dir / "0099_bad'name.sql"
        bad_file.write_text("SELECT 1")

        files = _migration_files(bad_dir)
        versions = [v for v, _n, _p in files]
        # The bad file must not appear in the parsed list
        assert 99 not in versions
        # The legitimate migrations are still present
        assert 1 in versions


class TestRequestsSchema:
    def test_requests_table_exists(self, ch_session):
        apply_all(ch_session)
        result = _query(
            ch_session,
            "SELECT count() FROM system.tables WHERE database='telemetry' AND name='requests'",
        )
        assert result == "1"

    def test_requests_order_by_leads_with_tenant_id(self, ch_session):
        apply_all(ch_session)
        # Check the CREATE TABLE statement stored in system.tables
        result = _query(
            ch_session,
            "SELECT sorting_key FROM system.tables WHERE database='telemetry' AND name='requests'",
        )
        # sorting_key should start with tenant_id (strip CSV quotes if present)
        sorting_key = result.strip('"')
        assert sorting_key.startswith("tenant_id"), (
            f"Expected sorting_key to start with 'tenant_id', got: {result!r}"
        )

    def test_requests_engine_is_replacing_merge_tree(self, ch_session):
        apply_all(ch_session)
        result = _query(
            ch_session,
            "SELECT engine FROM system.tables WHERE database='telemetry' AND name='requests'",
        )
        assert "ReplacingMergeTree" in result

    def test_requests_insert_and_read(self, ch_session):
        apply_all(ch_session)
        ch_session.query(
            """
            INSERT INTO telemetry.requests
              (tenant_id, id, timestamp, modality, provider, model_id)
            VALUES
              ('t1', 'req-1', '2025-01-15 10:00:00.000', 'llm', 'openai', 'gpt-4o')
            """,
            "CSV",
        )
        result = _query(
            ch_session,
            "SELECT id FROM telemetry.requests WHERE tenant_id='t1'",
        )
        assert "req-1" in result

    def test_requests_tenant_id_not_nullable(self, ch_session):
        apply_all(ch_session)
        result = _query(
            ch_session,
            """
            SELECT is_in_partition_key, type
            FROM system.columns
            WHERE database='telemetry' AND table='requests' AND name='tenant_id'
            """,
        )
        # LowCardinality(String) - should NOT contain Nullable
        assert "Nullable" not in result


class TestSessionsAgg:
    def test_sessions_agg_table_exists(self, ch_session):
        apply_all(ch_session)
        result = _query(
            ch_session,
            "SELECT count() FROM system.tables WHERE database='telemetry' AND name='sessions_agg'",
        )
        assert result == "1"

    def test_sessions_mv_exists(self, ch_session):
        apply_all(ch_session)
        result = _query(
            ch_session,
            "SELECT count() FROM system.tables WHERE database='telemetry' AND name='sessions_mv'",
        )
        assert result == "1"

    def test_sessions_agg_engine_is_aggregating_merge_tree(self, ch_session):
        apply_all(ch_session)
        result = _query(
            ch_session,
            "SELECT engine FROM system.tables WHERE database='telemetry' AND name='sessions_agg'",
        )
        assert "AggregatingMergeTree" in result


class TestTurnsSchema:
    def test_turns_table_exists(self, ch_session):
        apply_all(ch_session)
        result = _query(
            ch_session,
            "SELECT count() FROM system.tables WHERE database='telemetry' AND name='turns'",
        )
        assert result == "1"

    def test_turns_order_by_leads_with_tenant_id(self, ch_session):
        apply_all(ch_session)
        result = _query(
            ch_session,
            "SELECT sorting_key FROM system.tables WHERE database='telemetry' AND name='turns'",
        )
        sorting_key = result.strip('"')
        assert sorting_key.startswith("tenant_id"), (
            f"Expected turns sorting_key to start with 'tenant_id', got: {result!r}"
        )

    def test_turns_insert_and_read(self, ch_session):
        apply_all(ch_session)
        ch_session.query(
            """
            INSERT INTO telemetry.turns
              (tenant_id, session_id, id, timestamp, turn_index)
            VALUES
              ('t-turn', 'sess-1', 'turn-1', '2025-06-01 12:00:00.000', 0)
            """,
            "CSV",
        )
        result = _query(
            ch_session,
            "SELECT id FROM telemetry.turns WHERE tenant_id='t-turn'",
        )
        assert "turn-1" in result

    def test_turns_response_speed_ms_nullable_int64(self, ch_session):
        apply_all(ch_session)
        result = _query(
            ch_session,
            """
            SELECT type FROM system.columns
            WHERE database='telemetry' AND table='turns' AND name='response_speed_ms'
            """,
        )
        assert "Nullable" in result
        assert "Int64" in result

    def test_turns_agent_speak_start_ms_nullable(self, ch_session):
        apply_all(ch_session)
        result = _query(
            ch_session,
            """
            SELECT type FROM system.columns
            WHERE database='telemetry' AND table='turns' AND name='agent_speak_start_ms'
            """,
        )
        assert "Nullable" in result

    def test_turns_agent_speak_end_ms_nullable(self, ch_session):
        apply_all(ch_session)
        result = _query(
            ch_session,
            """
            SELECT type FROM system.columns
            WHERE database='telemetry' AND table='turns' AND name='agent_speak_end_ms'
            """,
        )
        assert "Nullable" in result
