-- Migration 0001: Create telemetry database and requests table
-- Engine: ReplacingMergeTree(_version) for idempotent upserts (single-node)
-- ORDER BY: (tenant_id, toStartOfHour(timestamp), id) - tenant_id MUST lead
-- PARTITION BY: toYYYYMM(timestamp) for time-based retention

CREATE DATABASE IF NOT EXISTS telemetry;

CREATE TABLE IF NOT EXISTS telemetry.requests (
  tenant_id      LowCardinality(String) DEFAULT '',
  id             String,
  timestamp      DateTime64(3, 'UTC'),
  project        LowCardinality(String) DEFAULT 'default',
  modality       LowCardinality(String),
  provider       LowCardinality(String),
  model_id       LowCardinality(String),
  input_units        Float64 DEFAULT 0,
  output_units       Float64 DEFAULT 0,
  cached_input_units Float64 DEFAULT 0,
  cost_usd           Float64 DEFAULT 0,
  pricing_source LowCardinality(String) DEFAULT '',
  ttfb_ms        Nullable(Float32),
  total_latency_ms Nullable(Float32),
  status         LowCardinality(String) DEFAULT 'success',
  fallback_from  LowCardinality(String) DEFAULT '',
  error_message  String DEFAULT '',
  session_id     String DEFAULT '',
  agent_id       LowCardinality(String) DEFAULT '',
  metadata       String DEFAULT '{}',
  _version       UInt64 MATERIALIZED toUInt64(toUnixTimestamp64Milli(timestamp))
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(timestamp)
ORDER BY (tenant_id, toStartOfHour(timestamp), id)
SETTINGS index_granularity = 8192, non_replicated_deduplication_window = 1000;
