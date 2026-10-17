-- What the worker reads from each project's LiveKit server (phase 05). Rows are the redacted
-- projections the console contract describes, never LiveKit's raw messages, identities or
-- secrets. layer_reads records every attempt, failed ones with their error class, so a gap in the
-- samples is never mistaken for an empty server. Samples expire after 30 days.

-- +goose Up
CREATE TABLE layer_reads (
    organisation_id LowCardinality(String),
    project_id      LowCardinality(String),
    layer           LowCardinality(String),
    read_at         DateTime64(3, 'UTC'),
    ok              Bool,
    error_class     LowCardinality(String),
    every_ms        UInt32
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(read_at)
ORDER BY (organisation_id, project_id, layer, read_at)
TTL toDateTime(read_at) + INTERVAL 30 DAY;

CREATE TABLE room_samples (
    organisation_id LowCardinality(String),
    project_id      LowCardinality(String),
    read_at         DateTime64(3, 'UTC'),
    room_sid        String,
    room            String CODEC(ZSTD(3))
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(read_at)
ORDER BY (organisation_id, project_id, room_sid, read_at)
TTL toDateTime(read_at) + INTERVAL 30 DAY;

CREATE TABLE participant_samples (
    organisation_id LowCardinality(String),
    project_id      LowCardinality(String),
    read_at         DateTime64(3, 'UTC'),
    room_sid        String,
    participant_sid String,
    state           LowCardinality(String),
    sip_call_status LowCardinality(String),
    participant     String CODEC(ZSTD(3))
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(read_at)
ORDER BY (organisation_id, project_id, room_sid, participant_sid, read_at)
TTL toDateTime(read_at) + INTERVAL 30 DAY;
