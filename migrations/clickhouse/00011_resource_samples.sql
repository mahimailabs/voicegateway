-- Container measurements and scrape outcomes remain separate: an exporter failure
-- never becomes zero CPU or memory. Nullable values preserve missing instrumentation.
-- +goose Up
CREATE TABLE resource_samples (
    organisation_id LowCardinality(String),
    project_id LowCardinality(String),
    endpoint_id String,
    read_at DateTime64(3, 'UTC'),
    observed_at DateTime64(3, 'UTC'),
    started_at Nullable(Float64),
    cpu_seconds Nullable(Float64),
    cpu_used Nullable(Float64),
    cpu_cores Nullable(Float64),
    memory_bytes Nullable(Float64),
    memory_limit_bytes Nullable(Float64)
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(read_at)
ORDER BY (organisation_id, project_id, endpoint_id, read_at)
TTL toDateTime(read_at) + INTERVAL 30 DAY
SETTINGS ttl_only_drop_parts = 1;

CREATE TABLE resource_endpoint_reads (
    organisation_id LowCardinality(String),
    project_id LowCardinality(String),
    endpoint_id String,
    read_at DateTime64(3, 'UTC'),
    ok Bool,
    error_class LowCardinality(String),
    every_ms UInt32
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(read_at)
ORDER BY (organisation_id, project_id, endpoint_id, read_at)
TTL toDateTime(read_at) + INTERVAL 30 DAY
SETTINGS ttl_only_drop_parts = 1;
