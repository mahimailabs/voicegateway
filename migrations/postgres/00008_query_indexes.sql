-- Indexes for reads that had none. calls_start_at serves getCallStats (StatsCalls): its predicate
-- is call_list's servable rule, so the planner matches it, and the key is the range it reads.
-- memberships_organisation serves the per-organisation member reads (the primary key leads with
-- user_id); project_keys_created_by serves the users foreign key's ON DELETE SET NULL. CONCURRENTLY
-- builds each without blocking writes, so this migration runs outside a transaction.

-- +goose NO TRANSACTION

-- +goose Up
CREATE INDEX CONCURRENTLY IF NOT EXISTS calls_start_at
    ON calls (organisation_id, project_id, (COALESCE(started_at, first_seen_at)), id)
    WHERE COALESCE(room_name, '') <> '' AND COALESCE(started_at, first_seen_at) IS NOT NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS memberships_organisation ON memberships (organisation_id, role);
CREATE INDEX CONCURRENTLY IF NOT EXISTS project_keys_created_by ON project_keys (created_by) WHERE created_by IS NOT NULL;

-- +goose Down
DROP INDEX CONCURRENTLY IF EXISTS project_keys_created_by;
DROP INDEX CONCURRENTLY IF EXISTS memberships_organisation;
DROP INDEX CONCURRENTLY IF EXISTS calls_start_at;
