-- User-managed call metadata is independent of worker activity and keyed by immutable call ID.
-- +goose Up
ALTER TABLE calls
    ADD COLUMN tags text[] NOT NULL DEFAULT '{}' CHECK (cardinality(tags) <= 32),
    ADD COLUMN archived boolean NOT NULL DEFAULT false,
    ADD COLUMN metadata_revision bigint NOT NULL DEFAULT 0 CHECK (metadata_revision BETWEEN 0 AND 9007199254740991);
CREATE INDEX calls_tags ON calls USING gin (tags);
CREATE INDEX calls_archive_scope ON calls (organisation_id, project_id, archived);
