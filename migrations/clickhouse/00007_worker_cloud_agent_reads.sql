-- What the worker reads of each project's agents (phase 05d). worker_samples holds what each listed
-- self-hosted worker metrics endpoint reported per worker (by its nodename label): load and active
-- jobs, null when the endpoint did not export them. worker_endpoint_reads records every attempt per
-- endpoint, failed ones with their error class, so an endpoint that stops answering reads as
-- unreachable, never as an idle worker. cloud_agent_reads holds the redacted projection of LiveKit
-- Cloud's agents and versions, one row per good read; secrets, attributes and owners never reach
-- it. Rows expire after 30 days.

-- +goose Up
CREATE TABLE worker_samples (
    organisation_id LowCardinality(String),
    project_id      LowCardinality(String),
    read_at         DateTime64(3, 'UTC'),
    endpoint_id     String,
    nodename        String,
    load            Nullable(Float64),
    active_jobs     Nullable(Int64)
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(read_at)
ORDER BY (organisation_id, project_id, endpoint_id, nodename, read_at)
TTL toDateTime(read_at) + INTERVAL 30 DAY;

CREATE TABLE worker_endpoint_reads (
    organisation_id LowCardinality(String),
    project_id      LowCardinality(String),
    endpoint_id     String,
    read_at         DateTime64(3, 'UTC'),
    ok              Bool,
    error_class     LowCardinality(String),
    every_ms        UInt32
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(read_at)
ORDER BY (organisation_id, project_id, endpoint_id, read_at)
TTL toDateTime(read_at) + INTERVAL 30 DAY;

CREATE TABLE cloud_agent_reads (
    organisation_id LowCardinality(String),
    project_id      LowCardinality(String),
    read_at         DateTime64(3, 'UTC'),
    agents          String CODEC(ZSTD(3))
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(read_at)
ORDER BY (organisation_id, project_id, read_at)
TTL toDateTime(read_at) + INTERVAL 30 DAY;
