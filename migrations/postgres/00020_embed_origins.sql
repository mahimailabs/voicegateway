-- +goose Up
-- The sites allowed to frame a project's widgets (decision 0023, 0034). NULL is not configured,
-- which allows none: missing configuration never authorises an arbitrary origin.
ALTER TABLE project_settings
    ADD COLUMN allowed_origins text[] CHECK (allowed_origins IS NULL OR cardinality(allowed_origins) <= 20);

-- +goose Down
ALTER TABLE project_settings DROP COLUMN allowed_origins;
