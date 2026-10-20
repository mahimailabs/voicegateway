-- +goose Up
CREATE TABLE project_settings (
    organisation_id text NOT NULL,
    project_id text NOT NULL,
    answer_ms double precision NOT NULL DEFAULT 1500 CHECK (answer_ms BETWEEN 100 AND 60000),
    join_ms double precision NOT NULL DEFAULT 2000 CHECK (join_ms BETWEEN 100 AND 60000),
    tts_start_ms double precision NOT NULL DEFAULT 1000 CHECK (tts_start_ms BETWEEN 100 AND 60000),
    revision bigint NOT NULL DEFAULT 0 CHECK (revision BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY (organisation_id, project_id),
    FOREIGN KEY (organisation_id, project_id) REFERENCES projects (organisation_id, id) ON DELETE CASCADE
);
