-- Explicit container resource targets. Exporter URLs may carry credentials and are sealed.
-- +goose Up
CREATE TABLE project_resource_endpoints (
    organisation_id text NOT NULL,
    project_id text NOT NULL,
    id text NOT NULL CHECK (id ~ '^re_[a-z0-9]{12}$'),
    host text NOT NULL CHECK (host <> ''),
    url_sealed bytea NOT NULL,
    kind text NOT NULL CHECK (kind IN ('sfu', 'worker')),
    target text NOT NULL CHECK (length(target) BETWEEN 1 AND 256),
    container_id text NOT NULL CHECK (length(container_id) BETWEEN 2 AND 1024 AND container_id <> '/'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organisation_id, project_id, id),
    UNIQUE (organisation_id, project_id, kind, target),
    FOREIGN KEY (organisation_id, project_id) REFERENCES projects (organisation_id, id) ON DELETE CASCADE
);
