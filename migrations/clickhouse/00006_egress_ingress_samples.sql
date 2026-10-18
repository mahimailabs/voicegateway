-- What the worker reads of each project's egress and ingress (phase 05c): the redacted projection of
-- every egress ListEgress lists and every endpoint ListIngress lists, one row per item per good read.
-- Upload credentials, stream keys, the credentials and query values of ingress URLs, and stream URLs
-- beyond their scheme and host never reach these tables. status is kept beside the projection so a
-- read can find where it changed, and updated_at is LiveKit's own time of the item's last change
-- (null when it reported none). Rows expire after 30 days.

-- +goose Up
CREATE TABLE egress_samples (
    organisation_id LowCardinality(String),
    project_id      LowCardinality(String),
    read_at         DateTime64(3, 'UTC'),
    egress_id       String,
    status          LowCardinality(String),
    updated_at      Nullable(DateTime64(9, 'UTC')),
    egress          String CODEC(ZSTD(3))
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(read_at)
ORDER BY (organisation_id, project_id, egress_id, read_at)
TTL toDateTime(read_at) + INTERVAL 30 DAY;

CREATE TABLE ingress_samples (
    organisation_id LowCardinality(String),
    project_id      LowCardinality(String),
    read_at         DateTime64(3, 'UTC'),
    ingress_id      String,
    status          LowCardinality(String),
    updated_at      Nullable(DateTime64(9, 'UTC')),
    ingress         String CODEC(ZSTD(3))
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(read_at)
ORDER BY (organisation_id, project_id, ingress_id, read_at)
TTL toDateTime(read_at) + INTERVAL 30 DAY;
