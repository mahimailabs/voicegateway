-- The instance admin role (decision 0041, docs/specs/instance-admin.md): a flag on the account,
-- set only from the command line. It is not an organisation role and grants none.

-- +goose Up
ALTER TABLE users ADD COLUMN instance_admin boolean NOT NULL DEFAULT false;

-- +goose Down
ALTER TABLE users DROP COLUMN instance_admin;
