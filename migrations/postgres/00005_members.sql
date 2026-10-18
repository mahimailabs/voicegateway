-- Members and roles (phase 06b, decision 0027). password_version counts password changes: sign-in
-- copies it into the session, and a session whose copy differs is signed out, so a password reset
-- signs a user out everywhere. scs/pgxstore keeps no user column to delete sessions by.

-- +goose Up
ALTER TABLE users ADD COLUMN password_version integer NOT NULL DEFAULT 0;
