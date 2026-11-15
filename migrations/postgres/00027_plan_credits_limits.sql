-- +goose Up
-- A plan is counted in credits (decision 0042): one profiled call minute is one credit, so the
-- column keeps its values and only its name changes. A null limit is unlimited.
ALTER TABLE organisation_entitlements RENAME COLUMN limit_minutes TO limit_credits;
ALTER TABLE organisation_entitlements
    ADD COLUMN retention_days integer CHECK (retention_days BETWEEN 1 AND 3650),
    ADD COLUMN max_projects integer CHECK (max_projects BETWEEN 0 AND 1000000),
    ADD COLUMN max_members integer CHECK (max_members BETWEEN 0 AND 1000000);

-- +goose Down
ALTER TABLE organisation_entitlements DROP COLUMN max_members, DROP COLUMN max_projects, DROP COLUMN retention_days;
ALTER TABLE organisation_entitlements RENAME COLUMN limit_credits TO limit_minutes;
