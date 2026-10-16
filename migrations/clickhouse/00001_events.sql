-- Every ingested envelope, as the worker stores it before calls are joined (phase 3, decision 0007).
-- dedupe_key is LiveKit's event id when the source sent one, else the stream entry id, so a
-- redelivered entry or a retried webhook collapses to one row. The worker checks it before
-- inserting, and ReplacingMergeTree folds any duplicate that slips past two workers racing.

-- +goose Up
CREATE TABLE events (
    organisation_id LowCardinality(String),
    project_id      LowCardinality(String),
    kind            LowCardinality(String),
    dedupe_key      String,
    event_id        String,
    stream_id       String,
    received_at     DateTime64(9, 'UTC'),
    payload         String CODEC(ZSTD(3)),
    inserted_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(received_at)
ORDER BY (organisation_id, project_id, kind, dedupe_key);
