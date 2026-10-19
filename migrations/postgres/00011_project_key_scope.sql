-- +goose Up
-- LiveKit keys belong to independent servers. Ingestion selects the project from
-- its URL before checking credentials, so keys must not reserve a global name.
ALTER TABLE projects DROP CONSTRAINT projects_livekit_api_key_key;

-- +goose Down
-- Refuse rollback when duplicate keys exist rather than deleting any project.
ALTER TABLE projects ADD CONSTRAINT projects_livekit_api_key_key UNIQUE (livekit_api_key);
