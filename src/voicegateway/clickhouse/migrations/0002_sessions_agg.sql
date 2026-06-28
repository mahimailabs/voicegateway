-- Migration 0002: sessions_agg AggregatingMergeTree + materialized view
-- Uses SimpleAggregateFunction so reads need NO -Merge combiner.
-- The MV eliminates per-record session UPSERT overhead.

CREATE TABLE IF NOT EXISTS telemetry.sessions_agg (
  tenant_id        LowCardinality(String),
  session_id       String,
  request_count    SimpleAggregateFunction(sum, UInt64),
  total_cost_usd   SimpleAggregateFunction(sum, Float64),
  started_at       SimpleAggregateFunction(min, DateTime64(3,'UTC')),
  ended_at         SimpleAggregateFunction(max, DateTime64(3,'UTC')),
  agent_id         SimpleAggregateFunction(anyLast, LowCardinality(String))
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(started_at)
ORDER BY (tenant_id, session_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry.sessions_mv TO telemetry.sessions_agg AS
SELECT tenant_id, session_id,
       count() AS request_count,
       sum(cost_usd) AS total_cost_usd,
       min(timestamp) AS started_at,
       max(timestamp) AS ended_at,
       anyLast(agent_id) AS agent_id
FROM telemetry.requests
WHERE session_id != ''
GROUP BY tenant_id, session_id;
