"""Tests for ClickHouseSink: chDB unit tests + testcontainers integration.

Two tiers:
1. chDB unit tests: verify row shape, column mapping, and timestamp conversion
   entirely in-process (no Docker required).
2. testcontainers integration tests (pytest.mark.integration): spin a real
   ClickHouse 26.1 container, insert 5 records via ClickHouseSink.flush(),
   assert exactly 5 rows, re-flush the identical batch with the same dedup
   token and assert still 5 rows (dedup), verify sessions_agg has the correct
   request_count and total_cost_usd with NO double-count.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib
import time
import uuid
from datetime import UTC, datetime

import pytest

MIGRATIONS_DIR = (
    pathlib.Path(__file__).parent.parent.parent / "clickhouse" / "migrations"
)

# ---------------------------------------------------------------------------
# Helpers shared by both tiers
# ---------------------------------------------------------------------------


def _make_record(
    record_id: str | None = None,
    *,
    cost_usd: float = 0.01,
    session_id: str = "sess-test",
    tenant_id: str = "acme",
    ts: float | None = None,
):
    """Return (RequestRecord, tenant_id) ready for ClickHouseSink._build_row."""
    from voicegateway.models.request_model import RequestRecord

    rid = record_id or str(uuid.uuid4())
    record = RequestRecord(
        id=rid,
        timestamp=ts or time.time(),
        modality="llm",
        provider="openai",
        model_id="openai/gpt-4o-mini",
        project="default",
        input_units=100.0,
        output_units=50.0,
        cached_input_units=0.0,
        cost_usd=cost_usd,
        pricing_source="voice-prices",
        ttfb_ms=None,
        total_latency_ms=None,
        status="success",
        fallback_from=None,
        error_message=None,
        metadata={},
        session_id=session_id,
        agent_id="agent-1",
    )
    return record, tenant_id


def _sink_instance():
    """Return a ClickHouseSink with no real client (sufficient for row-shape tests)."""
    from voicegateway.services.sinks import ClickHouseSink

    return ClickHouseSink(client=None)


# ---------------------------------------------------------------------------
# chDB unit tests (no Docker)
# ---------------------------------------------------------------------------

try:
    from chdb import session as chdb_session

    CHDB_AVAILABLE = True
except ImportError:
    CHDB_AVAILABLE = False


@pytest.fixture
def ch_session_unit(tmp_path):
    """Fresh chDB session for unit tests."""
    sess = chdb_session.Session(str(tmp_path / "ch_unit"))
    from voicegateway.clickhouse.migrate import apply_migrations_to_session

    apply_migrations_to_session(sess, MIGRATIONS_DIR)
    yield sess
    sess.close()


def _chdb_query(sess, sql: str) -> str:
    result = sess.query(sql, "CSV")
    raw = result.bytes() if hasattr(result, "bytes") else bytes(result)
    return raw.decode().strip()


@pytest.mark.skipif(not CHDB_AVAILABLE, reason="chdb not installed")
class TestClickHouseSinkRowShape:
    """chDB unit tests: verify that ClickHouseSink builds correct row dicts."""

    def test_row_dict_has_all_required_columns(self):
        """_build_row should produce keys matching all non-MATERIALIZED columns."""
        sink = _sink_instance()
        record, tenant_id = _make_record()
        row = sink._build_row(record, tenant_id)

        expected_columns = {
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
            "rated_price_usd",
            "rate_rule",
            "ttfb_ms",
            "total_latency_ms",
            "status",
            "fallback_from",
            "error_message",
            "session_id",
            "agent_id",
            "metadata",
        }
        assert set(row.keys()) == expected_columns

    def test_timestamp_is_datetime_utc(self):
        """timestamp column must be a datetime (DateTime64 compatible), not a float."""
        sink = _sink_instance()
        record, tenant_id = _make_record(ts=1_700_000_000.5)
        row = sink._build_row(record, tenant_id)
        ts = row["timestamp"]
        assert isinstance(ts, datetime), f"Expected datetime, got {type(ts)}"
        assert ts.tzinfo is UTC

    def test_timestamp_millisecond_precision(self):
        """Subsecond precision (ms) must be preserved in the datetime."""
        sink = _sink_instance()
        epoch_with_ms = 1_700_000_000.123
        record, tenant_id = _make_record(ts=epoch_with_ms)
        row = sink._build_row(record, tenant_id)
        ts = row["timestamp"]
        # 123ms -> microseconds = 123_000
        assert abs(ts.microsecond - 123_000) < 1_500, (
            f"Expected ~123000 us, got {ts.microsecond}"
        )

    def test_none_tenant_becomes_empty_string(self):
        """tenant_id=None must coerce to '' (ClickHouse DEFAULT, not NULL)."""
        sink = _sink_instance()
        record, _ = _make_record()
        row = sink._build_row(record, None)
        assert row["tenant_id"] == ""

    def test_none_fallback_from_becomes_empty_string(self):
        sink = _sink_instance()
        record, tenant_id = _make_record()
        row = sink._build_row(record, tenant_id)
        assert row["fallback_from"] == ""

    def test_none_error_message_becomes_empty_string(self):
        sink = _sink_instance()
        record, tenant_id = _make_record()
        row = sink._build_row(record, tenant_id)
        assert row["error_message"] == ""

    def test_metadata_serialized_to_json_string(self):
        from voicegateway.models.request_model import RequestRecord

        sink = _sink_instance()
        record = RequestRecord(
            id="meta-test",
            timestamp=time.time(),
            modality="llm",
            provider="openai",
            model_id="openai/gpt-4o",
            metadata={"key": "value", "num": 42},
        )
        row = sink._build_row(record, "t1")
        assert row["metadata"] == json.dumps({"key": "value", "num": 42})

    def test_dedup_token_is_deterministic(self):
        """Same sorted ids -> same token; different ids -> different token."""
        from voicegateway.services.sinks import ClickHouseSink

        ids_a = ["id-1", "id-2", "id-3"]
        ids_b = ["id-3", "id-1", "id-2"]  # same set, different order
        ids_c = ["id-1", "id-2", "id-4"]  # different set

        tok_a = ClickHouseSink._dedup_token(ids_a)
        tok_b = ClickHouseSink._dedup_token(ids_b)
        tok_c = ClickHouseSink._dedup_token(ids_c)

        assert tok_a == tok_b, "same ids reordered must produce same token"
        assert tok_a != tok_c, "different ids must produce different token"

    def test_dedup_token_is_sha256_hex(self):
        """Token must be the SHA-256 hex digest of sorted ids joined by newlines."""
        from voicegateway.services.sinks import ClickHouseSink

        ids = ["id-z", "id-a"]
        expected = hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()
        assert ClickHouseSink._dedup_token(ids) == expected

    def test_chdb_insert_and_read_via_sink_row(self, ch_session_unit):
        """Insert a sink-formatted row directly into chDB and read it back."""
        sink = _sink_instance()
        record, tenant_id = _make_record("chdb-row-1", cost_usd=0.05)
        row = sink._build_row(record, tenant_id)

        ts_str = row["timestamp"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        ch_session_unit.query(
            f"""
            INSERT INTO telemetry.requests
              (tenant_id, id, timestamp, project, modality, provider, model_id,
               input_units, output_units, cached_input_units, cost_usd,
               pricing_source, ttfb_ms, total_latency_ms, status,
               fallback_from, error_message, session_id, agent_id, metadata)
            VALUES
              ('{row["tenant_id"]}', '{row["id"]}', '{ts_str}',
               '{row["project"]}', '{row["modality"]}', '{row["provider"]}',
               '{row["model_id"]}', {row["input_units"]}, {row["output_units"]},
               {row["cached_input_units"]}, {row["cost_usd"]},
               '{row["pricing_source"]}',
               NULL, NULL,
               '{row["status"]}', '{row["fallback_from"]}',
               '{row["error_message"]}', '{row["session_id"]}',
               '{row["agent_id"]}', '{row["metadata"]}')
            """,
            "CSV",
        )
        result = _chdb_query(
            ch_session_unit,
            "SELECT id, cost_usd FROM telemetry.requests WHERE id='chdb-row-1'",
        )
        assert "chdb-row-1" in result
        assert "0.05" in result

    def test_chdb_rated_columns_round_trip(self, ch_session_unit):
        """Migration 0004 adds rated_price_usd + rate_rule; they store and read back."""
        sink = _sink_instance()
        record, tenant_id = _make_record("chdb-rated-1", cost_usd=0.010)
        record.rated_price_usd = 0.015
        record.rate_rule = "cost_plus:1.5"
        row = sink._build_row(record, tenant_id)

        ts_str = row["timestamp"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        ch_session_unit.query(
            f"""
            INSERT INTO telemetry.requests
              (tenant_id, id, timestamp, project, modality, provider, model_id,
               cost_usd, rated_price_usd, rate_rule)
            VALUES
              ('{row["tenant_id"]}', '{row["id"]}', '{ts_str}',
               '{row["project"]}', '{row["modality"]}', '{row["provider"]}',
               '{row["model_id"]}', {row["cost_usd"]},
               {row["rated_price_usd"]}, '{row["rate_rule"]}')
            """,
            "CSV",
        )
        result = _chdb_query(
            ch_session_unit,
            "SELECT rated_price_usd, rate_rule FROM telemetry.requests "
            "WHERE id='chdb-rated-1'",
        )
        assert "0.015" in result
        assert "cost_plus:1.5" in result


# ---------------------------------------------------------------------------
# testcontainers integration tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def clickhouse_container_sink():
    """One ClickHouse 26.1 container for the full sink test module."""
    from testcontainers.clickhouse import ClickHouseContainer

    with ClickHouseContainer(image="clickhouse/clickhouse-server:26.1") as container:
        yield container


@pytest.fixture(scope="module")
def ch_async_client_sink(clickhouse_container_sink):
    """Async clickhouse-connect client; runs apply_migrations on first fixture use."""
    import clickhouse_connect

    from voicegateway.clickhouse.migrate import apply_migrations

    host = clickhouse_container_sink.get_container_host_ip()
    port = int(clickhouse_container_sink.get_exposed_port(8123))
    client = asyncio.run(
        clickhouse_connect.get_async_client(
            host=host,
            port=port,
            username=clickhouse_container_sink.username,
            password=clickhouse_container_sink.password,
            database=clickhouse_container_sink.dbname,
        )
    )
    asyncio.run(apply_migrations(client, MIGRATIONS_DIR))
    yield client
    try:
        asyncio.run(client.close())
    except Exception:
        pass


@pytest.fixture(scope="module")
def sink_host_port(clickhouse_container_sink):
    """(host, port) tuple for ClickHouseSink construction."""
    host = clickhouse_container_sink.get_container_host_ip()
    port = int(clickhouse_container_sink.get_exposed_port(8123))
    return host, port


@pytest.fixture(scope="module")
def sink_credentials(clickhouse_container_sink):
    """(username, password) read from the container, not hardcoded.

    Sourcing from the fixture keeps the tests correct if a future image
    changes the default credentials.
    """
    return clickhouse_container_sink.username, clickhouse_container_sink.password


@pytest.fixture(scope="module")
def ch_sync_client_sink(clickhouse_container_sink):
    """Sync client for assertion queries."""
    import clickhouse_connect

    host = clickhouse_container_sink.get_container_host_ip()
    port = int(clickhouse_container_sink.get_exposed_port(8123))
    client = clickhouse_connect.get_client(
        host=host,
        port=port,
        username=clickhouse_container_sink.username,
        password=clickhouse_container_sink.password,
        database=clickhouse_container_sink.dbname,
    )
    yield client
    client.close()


def _wait_for_rows(sync_client, sql: str, expected_min: int = 1, timeout: float = 8.0):
    """Poll until at least expected_min rows appear, return result_rows."""
    deadline = time.monotonic() + timeout
    rows = []
    while time.monotonic() < deadline:
        result = sync_client.query(sql)
        rows = result.result_rows
        if len(rows) >= expected_min:
            return rows
        time.sleep(0.25)
    return rows


@pytest.mark.integration
class TestClickHouseSinkIntegration:
    """Integration: ClickHouseSink against a live ClickHouse 26.1 container."""

    def _make_sink(self, host, port, username, password):
        from voicegateway.services.sinks import ClickHouseSink

        return asyncio.run(
            ClickHouseSink.create(
                host=host,
                port=port,
                username=username,
                password=password,
                database="telemetry",
            )
        )

    def test_five_records_inserted_exactly_once(
        self,
        sink_host_port,
        sink_credentials,
        ch_sync_client_sink,
        ch_async_client_sink,
    ):
        """POST 5 records -> exactly 5 rows in requests; sessions_agg correct."""
        host, port = sink_host_port
        username, password = sink_credentials
        sink = self._make_sink(host, port, username, password)

        session_id = f"sess-{uuid.uuid4()}"
        tenant_id = "tenant-batch5"
        records_and_tenants = []
        for i in range(5):
            record, _ = _make_record(
                f"batch5-{i}",
                cost_usd=0.01,
                session_id=session_id,
                tenant_id=tenant_id,
            )
            records_and_tenants.append((record, tenant_id))

        async def _log_and_flush():
            from voicegateway.inference.session.context import set_tenant

            for record, tid in records_and_tenants:
                set_tenant(tid)
                await sink.log_request(record)
            await sink.flush()
            await sink.aclose()

        asyncio.run(_log_and_flush())

        rows = _wait_for_rows(
            ch_sync_client_sink,
            f"SELECT id FROM telemetry.requests WHERE session_id='{session_id}'",
            expected_min=5,
        )
        assert len(rows) == 5, f"Expected 5 rows, got {len(rows)}: {rows}"

        # sessions_agg via MV
        agg_rows = _wait_for_rows(
            ch_sync_client_sink,
            f"SELECT request_count, total_cost_usd FROM telemetry.sessions_agg "
            f"WHERE tenant_id='{tenant_id}' AND session_id='{session_id}'",
            expected_min=1,
        )
        assert agg_rows, "sessions_agg not populated"
        total_count = sum(r[0] for r in agg_rows)
        total_cost = sum(r[1] for r in agg_rows)
        assert total_count >= 5, f"request_count should be >=5, got {total_count}"
        assert abs(total_cost - 0.05) < 1e-6, (
            f"total_cost_usd should be 0.05, got {total_cost}"
        )

    def test_repost_identical_batch_no_double_count(
        self,
        sink_host_port,
        sink_credentials,
        ch_sync_client_sink,
        ch_async_client_sink,
    ):
        """Re-flushing the same records with the same dedup token -> still N rows."""
        host, port = sink_host_port
        username, password = sink_credentials
        session_id = f"sess-dedup-{uuid.uuid4()}"
        tenant_id = "tenant-dedup"

        record_ids = [f"dedup-{i}-{uuid.uuid4()}" for i in range(3)]

        async def _first_flush():
            from voicegateway.inference.session.context import set_tenant
            from voicegateway.services.sinks import ClickHouseSink

            sink = await ClickHouseSink.create(
                host=host,
                port=port,
                username=username,
                password=password,
                database="telemetry",
            )
            set_tenant(tenant_id)
            for rid in record_ids:
                record, _ = _make_record(
                    rid, cost_usd=0.02, session_id=session_id, tenant_id=tenant_id
                )
                await sink.log_request(record)
            token = ClickHouseSink._dedup_token([row["id"] for row in sink._buffer])
            await sink.flush()
            return token

        token = asyncio.run(_first_flush())

        # Wait for first flush to land
        rows_after_first = _wait_for_rows(
            ch_sync_client_sink,
            f"SELECT id FROM telemetry.requests WHERE session_id='{session_id}'",
            expected_min=3,
        )
        assert len(rows_after_first) == 3, (
            f"Expected 3 rows after first flush, got {len(rows_after_first)}"
        )

        # Re-flush an identical batch with the same token
        async def _second_flush():
            from voicegateway.inference.session.context import set_tenant
            from voicegateway.services.sinks import ClickHouseSink

            sink2 = await ClickHouseSink.create(
                host=host,
                port=port,
                username=username,
                password=password,
                database="telemetry",
            )
            set_tenant(tenant_id)
            for rid in record_ids:
                record, _ = _make_record(
                    rid, cost_usd=0.02, session_id=session_id, tenant_id=tenant_id
                )
                await sink2.log_request(record)
            recomputed = ClickHouseSink._dedup_token(
                [row["id"] for row in sink2._buffer]
            )
            assert recomputed == token, (
                "Token must be deterministic across sink instances"
            )
            await sink2.flush()
            await sink2.aclose()

        asyncio.run(_second_flush())

        # Allow any in-flight async_insert to settle
        time.sleep(1.0)

        # FINAL collapses ReplacingMergeTree duplicates immediately
        rows_final = ch_sync_client_sink.query(
            f"SELECT id FROM telemetry.requests FINAL WHERE session_id='{session_id}'"
        ).result_rows
        assert len(rows_final) == 3, (
            f"Expected 3 rows FINAL after re-flush (dedup), got {len(rows_final)}: "
            f"{rows_final}"
        )

    def test_tenant_captured_at_enqueue_not_flush(
        self, sink_host_port, sink_credentials, ch_sync_client_sink
    ):
        """Tenant must be stamped at log_request time, not at flush time."""
        host, port = sink_host_port
        username, password = sink_credentials
        session_id = f"sess-tenant-{uuid.uuid4()}"

        async def _log_with_tenant_then_clear():
            from voicegateway.inference.session.context import (
                reset_tenant_id,
                set_tenant,
            )
            from voicegateway.services.sinks import ClickHouseSink

            sink = await ClickHouseSink.create(
                host=host,
                port=port,
                username=username,
                password=password,
                database="telemetry",
            )
            # Set tenant BEFORE log_request (this is the correct path)
            set_tenant("tenant-enqueue-test")
            record, _ = _make_record(
                f"enqueue-{uuid.uuid4()}",
                session_id=session_id,
                tenant_id="tenant-enqueue-test",
            )
            await sink.log_request(record)
            # Clear the tenant BEFORE flush to prove capture happened at enqueue
            reset_tenant_id()
            await sink.flush()
            await sink.aclose()

        asyncio.run(_log_with_tenant_then_clear())

        rows = _wait_for_rows(
            ch_sync_client_sink,
            f"SELECT tenant_id FROM telemetry.requests WHERE session_id='{session_id}'",
            expected_min=1,
        )
        assert rows, "No row found after tenant-enqueue test"
        assert rows[0][0] == "tenant-enqueue-test", (
            f"Expected tenant 'tenant-enqueue-test', got {rows[0][0]!r}"
        )


# ---------------------------------------------------------------------------
# Routing test: ingest handler uses a fresh ClickHouseSink when ch_client set
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestIngestCHRouting:
    """Verify the ingest handler routes to ClickHouse when app.state.ch_client is set.

    Uses a recording double for the ClickHouse client to confirm:
    - a fresh ClickHouseSink is built per request (not a stale shared instance),
    - flush() is awaited exactly once per batch,
    - the SQLite storage path is NOT touched.
    """

    def test_ingest_routes_to_clickhouse_when_ch_client_set(self):
        """When ch_client is on app.state, the handler inserts via ClickHouseSink."""
        import os
        import tempfile

        import yaml
        from httpx import ASGITransport, AsyncClient

        from voicegateway.core.gateway import Gateway
        from voicegateway.repository import api_keys_repository as api_keys
        from voicegateway.server import build_app

        # Minimal config (cost tracking on so storage is not None).
        cfg = {
            "providers": {"openai": {"api_key": "test-key"}},
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "projects": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": True},
        }

        # Keep tmp alive for the full test (not a context manager).
        tmp_obj = tempfile.TemporaryDirectory()
        tmp = tmp_obj.name
        try:
            os.environ["VOICEGW_DB_PATH"] = os.path.join(tmp, "test.db")
            cfg_path = os.path.join(tmp, "voicegw.yaml")
            with open(cfg_path, "w") as f:
                yaml.dump(cfg, f)
            gw = Gateway(config_path=cfg_path)

            # Build a recording double for the ClickHouse client.
            inserts: list[dict] = []

            class _FakeClient:
                async def insert(self, table, data, *, column_names, settings):
                    inserts.append(
                        {"table": table, "rows": len(data), "settings": settings}
                    )

                async def close(self):
                    pass

            fake_ch = _FakeClient()

            app = build_app(gw, enable_mcp_sse=False, enable_dashboard=False)
            # Inject the fake client (bypassing the real lifespan).
            app.state.ch_client = fake_ch

            # Track storage.log_request calls to prove SQLite path is skipped.
            sqlite_calls: list = []
            real_log = gw.storage.log_request

            async def _spy_log(record):
                sqlite_calls.append(record)
                return await real_log(record)

            gw.storage.log_request = _spy_log  # type: ignore[method-assign]

            payload = [
                {
                    "id": f"ch-route-{i}",
                    "timestamp": 1_000_000.0,
                    "modality": "llm",
                    "model_id": "openai/gpt-4o-mini",
                    "provider": "openai",
                    "project": "default",
                    "input_units": 10.0,
                    "output_units": 5.0,
                    "cost_usd": 0.001,
                }
                for i in range(3)
            ]

            async def _run():
                await gw.storage._ensure_initialized()
                async with gw.storage._conn.session() as db:
                    created = await api_keys.create_api_key(
                        db, name="bot", tenant_id="t1"
                    )

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as c:
                    resp = await c.post(
                        "/v1/ingest",
                        headers={"Authorization": f"Bearer {created.plaintext}"},
                        json=payload,
                    )

                assert resp.status_code == 200
                body = resp.json()
                assert body["accepted"] == 3, f"Expected 3 accepted, got {body}"
                assert body["duplicates"] == 0

                # One columnar insert call (flush was awaited exactly once).
                assert len(inserts) == 1, f"Expected 1 insert call, got {inserts}"
                assert inserts[0]["table"] == "telemetry.requests"
                assert inserts[0]["rows"] == 3

                # SQLite was NOT touched on the ClickHouse path.
                assert sqlite_calls == [], (
                    f"SQLite storage.log_request called {len(sqlite_calls)} time(s); "
                    "expected 0 on the ClickHouse path"
                )

            asyncio.run(_run())
        finally:
            tmp_obj.cleanup()

    def test_ingest_returns_503_when_clickhouse_flush_fails(self):
        """A failed ClickHouse flush returns 503 so the client retries the batch.

        The deterministic insert_deduplication_token makes the re-POST a no-op
        if the rows already landed, so a 503 gives lossless at-least-once
        delivery. SQLite must NOT be written as a fallback on this path.
        """
        import os
        import tempfile

        import yaml
        from httpx import ASGITransport, AsyncClient

        from voicegateway.core.gateway import Gateway
        from voicegateway.repository import api_keys_repository as api_keys
        from voicegateway.server import build_app

        cfg = {
            "providers": {"openai": {"api_key": "test-key"}},
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "projects": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": True},
        }

        tmp_obj = tempfile.TemporaryDirectory()
        tmp = tmp_obj.name
        try:
            os.environ["VOICEGW_DB_PATH"] = os.path.join(tmp, "test503.db")
            cfg_path = os.path.join(tmp, "voicegw.yaml")
            with open(cfg_path, "w") as f:
                yaml.dump(cfg, f)
            gw = Gateway(config_path=cfg_path)

            class _FailingClient:
                async def insert(self, table, data, *, column_names, settings):
                    raise RuntimeError("clickhouse unreachable")

                async def close(self):
                    pass

            app = build_app(gw, enable_mcp_sse=False, enable_dashboard=False)
            app.state.ch_client = _FailingClient()

            sqlite_calls: list = []
            real_log = gw.storage.log_request

            async def _spy_log(record):
                sqlite_calls.append(record)
                return await real_log(record)

            gw.storage.log_request = _spy_log  # type: ignore[method-assign]

            async def _run():
                await gw.storage._ensure_initialized()
                async with gw.storage._conn.session() as db:
                    created = await api_keys.create_api_key(db, name="bot503")

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as c:
                    resp = await c.post(
                        "/v1/ingest",
                        headers={"Authorization": f"Bearer {created.plaintext}"},
                        json=[
                            {
                                "id": "ch-fail-1",
                                "timestamp": 1_000_000.0,
                                "modality": "llm",
                                "model_id": "openai/gpt-4o-mini",
                                "provider": "openai",
                                "project": "default",
                                "input_units": 10.0,
                                "output_units": 5.0,
                                "cost_usd": 0.001,
                            }
                        ],
                    )

                assert resp.status_code == 503, (
                    f"Expected 503 on flush failure, got {resp.status_code}"
                )
                assert sqlite_calls == [], (
                    "SQLite must not be written as a fallback on the ClickHouse path"
                )

            asyncio.run(_run())
        finally:
            tmp_obj.cleanup()

    def test_ingest_sqlite_path_untouched_when_ch_client_absent(self):
        """Confirm ch_client=None falls through to the SQLite path unchanged."""
        import os
        import tempfile

        import yaml
        from httpx import ASGITransport, AsyncClient

        from voicegateway.core.gateway import Gateway
        from voicegateway.repository import api_keys_repository as api_keys
        from voicegateway.server import build_app

        cfg = {
            "providers": {"openai": {"api_key": "test-key"}},
            "models": {"stt": {}, "llm": {}, "tts": {}},
            "projects": {},
            "fallbacks": {"stt": [], "llm": [], "tts": []},
            "cost_tracking": {"enabled": True},
        }

        tmp_obj = tempfile.TemporaryDirectory()
        tmp = tmp_obj.name
        try:
            os.environ["VOICEGW_DB_PATH"] = os.path.join(tmp, "test2.db")
            cfg_path = os.path.join(tmp, "voicegw.yaml")
            with open(cfg_path, "w") as f:
                yaml.dump(cfg, f)
            gw = Gateway(config_path=cfg_path)

            app = build_app(gw, enable_mcp_sse=False, enable_dashboard=False)
            # No ch_client set: simulates default startup without ClickHouse.
            app.state.ch_client = None

            async def _run():
                await gw.storage._ensure_initialized()
                async with gw.storage._conn.session() as db:
                    created = await api_keys.create_api_key(db, name="bot2")

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as c:
                    resp = await c.post(
                        "/v1/ingest",
                        headers={"Authorization": f"Bearer {created.plaintext}"},
                        json=[
                            {
                                "id": "sqlite-route-1",
                                "timestamp": 1_000_000.0,
                                "modality": "llm",
                                "model_id": "openai/gpt-4o-mini",
                                "provider": "openai",
                                "project": "default",
                                "input_units": 10.0,
                                "output_units": 5.0,
                                "cost_usd": 0.001,
                            }
                        ],
                    )

                assert resp.status_code == 200
                assert resp.json() == {"accepted": 1, "duplicates": 0}

                rows = await gw.storage.get_recent_requests(limit=10)
                assert any(r["id"] == "sqlite-route-1" for r in rows), (
                    "Record not found in SQLite storage"
                )

            asyncio.run(_run())
        finally:
            tmp_obj.cleanup()
