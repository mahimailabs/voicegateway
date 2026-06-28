-- Migration 0003: per-turn rows table
-- Maps to the Turn ORM model (src/voicegateway/models/turn_model.py).
-- tenant_id leads the ORDER BY for tenant-scoped range scans.

CREATE TABLE IF NOT EXISTS telemetry.turns (
  tenant_id              LowCardinality(String) DEFAULT '',
  session_id             String,
  id                     String,
  timestamp              DateTime64(3, 'UTC'),
  turn_index             Int32   DEFAULT 0,
  caller_speak_start_ms  Int64   DEFAULT 0,
  caller_speak_end_ms    Int64   DEFAULT 0,
  agent_speak_start_ms   Nullable(Int64),
  agent_speak_end_ms     Nullable(Int64),
  response_speed         Nullable(Float32),
  agent_id               LowCardinality(String) DEFAULT ''
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(timestamp)
ORDER BY (tenant_id, session_id, timestamp);
