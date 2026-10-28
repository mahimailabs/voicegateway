-- User-managed call metadata is independent of worker activity and keyed by immutable call ID.
-- +goose Up
ALTER TABLE calls
    ADD COLUMN tags text[] NOT NULL DEFAULT '{}' CHECK (cardinality(tags) <= 32),
    ADD COLUMN archived boolean NOT NULL DEFAULT false,
    ADD COLUMN metadata_revision bigint NOT NULL DEFAULT 0 CHECK (metadata_revision BETWEEN 0 AND 9007199254740991);
CREATE INDEX calls_tags ON calls USING gin (tags);
CREATE INDEX calls_archive_scope ON calls (organisation_id, project_id, archived);

ALTER TABLE projects ADD COLUMN call_metadata_revision bigint NOT NULL DEFAULT 0;
-- +goose StatementBegin
CREATE FUNCTION bump_call_metadata_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE projects SET call_metadata_revision = call_metadata_revision + 1
    WHERE organisation_id = NEW.organisation_id AND id = NEW.project_id;
    RETURN NEW;
END;
$$;
-- +goose StatementEnd
CREATE TRIGGER call_metadata_changed AFTER UPDATE OF tags, archived ON calls
FOR EACH ROW EXECUTE FUNCTION bump_call_metadata_revision();
