-- +goose Up
-- The sites allowed to frame a project's widgets (decision 0023, 0034). NULL is not configured,
-- which allows none: missing configuration never authorises an arbitrary origin. The list has its
-- own revision: the settings revision versions the thresholds the issue engine evaluates against,
-- and a site change must not send every call back for evaluation.
ALTER TABLE project_settings
    ADD COLUMN allowed_origins text[] CHECK (allowed_origins IS NULL OR cardinality(allowed_origins) <= 20),
    ADD COLUMN allowed_origins_revision bigint NOT NULL DEFAULT 0;

-- +goose Down
ALTER TABLE project_settings DROP COLUMN allowed_origins, DROP COLUMN allowed_origins_revision;
