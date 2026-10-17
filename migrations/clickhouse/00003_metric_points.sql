-- Usage counter points from agents' OTLP metric exports (phase 04b). LiveKit Agents 1.8.7 exports
-- lk.agents.usage.* counters with delta temporality, so a call's points add up. One export can carry
-- points of two jobs, so each point carries its own call id. A redelivered export folds onto the
-- same rows: a delta point is unique by metric, attributes and its start and end time.

-- +goose Up
-- +goose StatementBegin
CREATE TABLE metric_points (
    organisation_id LowCardinality(String),
    project_id      LowCardinality(String),
    name            LowCardinality(String),
    call_id         String,
    value           Float64,
    start_time      DateTime64(9, 'UTC'),
    end_time        DateTime64(9, 'UTC'),
    room_id         String,
    job_id          String,
    attributes      Map(String, String),
    -- The attributes as sorted key=value lines, the fold key, since a Map cannot be sorted on.
    attributes_key  String,
    stream_id       String,
    inserted_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(end_time)
ORDER BY (organisation_id, project_id, name, attributes_key, start_time, end_time)
-- +goose StatementEnd
