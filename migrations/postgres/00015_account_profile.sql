-- +goose Up
ALTER TABLE users
    ADD COLUMN display_name text CHECK (display_name IS NULL OR char_length(display_name) BETWEEN 1 AND 100),
    ADD COLUMN profile_revision bigint NOT NULL DEFAULT 0 CHECK (profile_revision BETWEEN 0 AND 9007199254740991);
