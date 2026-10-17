-- Each project's SIP configuration as the worker read it (phase 05b): the redacted projection of
-- its trunks, dispatch rules and LiveKit phone numbers, one row per good read. Passwords, usernames,
-- PINs, header values, room prefixes and names never reach this table. Rows expire after 30 days.

-- +goose Up
CREATE TABLE sip_reads (
    organisation_id LowCardinality(String),
    project_id      LowCardinality(String),
    read_at         DateTime64(3, 'UTC'),
    config          String CODEC(ZSTD(3))
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(read_at)
ORDER BY (organisation_id, project_id, read_at)
TTL toDateTime(read_at) + INTERVAL 30 DAY;
