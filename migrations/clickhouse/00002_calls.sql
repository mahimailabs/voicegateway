-- Call links for stored events and spans (phase 04a). The call index itself is in Postgres.
-- spans holds one row per OTLP span, folded on trace and span id so a redelivered export is one
-- row. trace_calls links a trace to the call one of its spans joined, for spans that carry no id.
-- unmatched holds one row per event or trace that resolved to no call, so the count survives
-- restarts. Two workers sweeping at once can write a trace twice until a merge folds it, so a
-- reader counts with FINAL or uniqExact(key).

-- +goose Up
ALTER TABLE events ADD COLUMN call_id String DEFAULT '' AFTER kind;

-- +goose StatementBegin
CREATE TABLE spans (
    organisation_id LowCardinality(String),
    project_id      LowCardinality(String),
    trace_id        FixedString(32),
    span_id         FixedString(16),
    parent_span_id  String,
    call_id         String,
    name            String,
    kind            LowCardinality(String),
    -- Null when the exporter sent no time: unknown, never the epoch.
    start_time      Nullable(DateTime64(9, 'UTC')),
    end_time        Nullable(DateTime64(9, 'UTC')),
    status_code     LowCardinality(String),
    room_id         String,
    job_id          String,
    scope_name      LowCardinality(String),
    attributes      Map(String, String) CODEC(ZSTD(3)),
    resource        Map(String, String) CODEC(ZSTD(3)),
    stream_id       String,
    inserted_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(ifNull(start_time, inserted_at))
ORDER BY (organisation_id, project_id, trace_id, span_id)
-- +goose StatementEnd

-- +goose StatementBegin
CREATE TABLE trace_calls (
    organisation_id LowCardinality(String),
    project_id      LowCardinality(String),
    trace_id        FixedString(32),
    call_id         String,
    inserted_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY (organisation_id, project_id, trace_id)
-- +goose StatementEnd

-- +goose StatementBegin
CREATE TABLE unmatched (
    organisation_id LowCardinality(String),
    project_id      LowCardinality(String),
    kind            LowCardinality(String),
    key             String,
    reason          LowCardinality(String),
    inserted_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY (organisation_id, project_id, kind, key)
-- +goose StatementEnd
