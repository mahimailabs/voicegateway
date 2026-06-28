"""ClickHouse telemetry store: schema DDL, migration runner, and helpers."""

from voicegateway.clickhouse.migrate import apply_migrations

__all__ = ["apply_migrations"]
