package pgstore

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/mahimailabs/voicegateway/internal/event"

	"github.com/jackc/pgx/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// PutEntitlement serializes changes per organisation. A repeated revision must repeat the
// same body; older revisions cannot reactivate a canceled or exhausted subscription.
func (s *Store) PutEntitlement(ctx context.Context, org string, e store.Entitlement) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		var id string
		if err := tx.QueryRow(ctx, "SELECT id FROM organisations WHERE id=$1 FOR UPDATE", org).Scan(&id); err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				return store.ErrNotFound
			}
			return err
		}
		var old store.Entitlement
		err := tx.QueryRow(ctx, "SELECT from_at,to_at,limit_credits,retention_days,max_projects,max_members,active,revision FROM organisation_entitlements WHERE organisation_id=$1", org).
			Scan(&old.From, &old.To, &old.LimitCredits, &old.RetentionDays, &old.MaxProjects, &old.MaxMembers, &old.Active, &old.Revision)
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return err
		}
		if err == nil && old.Revision >= e.Revision {
			if old.Revision == e.Revision && old.SameLimits(e) {
				return nil
			}
			return store.ErrEntitlementRevision
		}
		first := errors.Is(err, pgx.ErrNoRows)
		if first {
			if _, err := tx.Exec(ctx, "LOCK TABLE calls IN SHARE ROW EXCLUSIVE MODE"); err != nil {
				return err
			}
		}
		if _, err := tx.Exec(ctx, `INSERT INTO organisation_entitlements(organisation_id,from_at,to_at,limit_credits,active,revision,retention_days,max_projects,max_members)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT(organisation_id) DO UPDATE SET from_at=$2,to_at=$3,limit_credits=$4,active=$5,revision=$6,
            retention_days=$7,max_projects=$8,max_members=$9`, org, e.From, e.To, e.LimitCredits, e.Active, e.Revision, e.RetentionDays, e.MaxProjects, e.MaxMembers); err != nil {
			return err
		}
		if !first {
			return nil
		}
		// Backfill existing calls on first activation. Subsequent changes are captured by the
		// trigger. DO NOTHING prevents a stale backfill overwriting a concurrent worker update.
		_, err = tx.Exec(ctx, `INSERT INTO profiled_call_usage(call_id,organisation_id,effective_end,seconds)
            SELECT id,organisation_id, CASE end_state WHEN 'observed' THEN ended_at WHEN 'inferred' THEN last_activity_at END,
            CASE WHEN started_at IS NOT NULL AND
              (CASE end_state WHEN 'observed' THEN ended_at WHEN 'inferred' THEN last_activity_at END)>=started_at
              THEN extract(epoch FROM (CASE end_state WHEN 'observed' THEN ended_at WHEN 'inferred' THEN last_activity_at END)-started_at) ELSE NULL END
            FROM calls WHERE organisation_id=$1 ON CONFLICT(call_id) DO NOTHING`, org)
		return err
	})
}

// EntitlementUsage reads one snapshot of the entitlement and its durable measured usage.
func (s *Store) EntitlementUsage(ctx context.Context, org string) (store.EntitlementUsage, error) {
	var u store.EntitlementUsage
	err := s.pool.QueryRow(ctx, `SELECT e.from_at,e.to_at,e.limit_credits,e.retention_days,e.max_projects,e.max_members,e.active,e.revision,
        coalesce((SELECT floor(sum(seconds))::bigint FROM profiled_call_usage u
            WHERE u.organisation_id=e.organisation_id AND u.effective_end>=e.from_at AND u.effective_end<e.to_at),0),
        (SELECT count(*) FROM calls c WHERE c.organisation_id=e.organisation_id
          AND c.first_seen_at>=e.from_at AND c.first_seen_at<e.to_at AND c.end_state<>'open'
          AND (c.started_at IS NULL OR
            (CASE c.end_state WHEN 'observed' THEN c.ended_at WHEN 'inferred' THEN c.last_activity_at END) IS NULL OR
            (CASE c.end_state WHEN 'observed' THEN c.ended_at WHEN 'inferred' THEN c.last_activity_at END)<c.started_at)),
        (SELECT count(*) FROM calls c WHERE c.organisation_id=e.organisation_id
          AND c.first_seen_at>=e.from_at AND c.first_seen_at<e.to_at AND c.end_state='open')
        FROM organisation_entitlements e WHERE e.organisation_id=$1`, org).
		Scan(&u.From, &u.To, &u.LimitCredits, &u.RetentionDays, &u.MaxProjects, &u.MaxMembers, &u.Active, &u.Revision, &u.CallSeconds, &u.UnmeasuredCalls, &u.OpenCalls)
	if errors.Is(err, pgx.ErrNoRows) {
		return u, store.ErrNotFound
	}
	if err != nil {
		return u, fmt.Errorf("entitlement usage: %w", err)
	}
	return u, nil
}

// KnownQuotaCalls requires every identity to match the authenticated project and billing period.
// Closed calls remain eligible so the final session report can arrive after room_finished.
func (s *Store) KnownQuotaCalls(ctx context.Context, org, project string, refs []event.CallIdentity, from, to time.Time) (bool, error) {
	if len(refs) == 0 {
		return false, nil
	}
	for _, ref := range refs {
		var exists bool
		err := s.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM calls WHERE organisation_id=$1 AND project_id=$2
   AND (($3<>'' AND room_sid=$3) OR ($3='' AND $4<>'' AND job_id=$4))
   AND coalesce(started_at,first_seen_at)<$6
   AND (end_state='open' OR coalesce(ended_at,last_activity_at)>=$5)) OR EXISTS(
    SELECT 1 FROM telemetry_admissions WHERE organisation_id=$1 AND project_id=$2
    AND (($3<>'' AND identity_kind='room' AND identity_id=$3) OR ($3='' AND identity_kind='job' AND identity_id=$4))
    AND from_at=$5 AND to_at=$6)`, org, project, ref.RoomSID, ref.JobID, from, to).Scan(&exists)
		if err != nil {
			return false, err
		}
		if !exists {
			return false, nil
		}
	}
	return true, nil
}

// RememberQuotaCalls is deliberately separate from the call index: only the worker joins calls.
func (s *Store) RememberQuotaCalls(ctx context.Context, org, project string, refs []event.CallIdentity, now time.Time) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		for _, ref := range refs {
			for kind, id := range map[string]string{"room": ref.RoomSID, "job": ref.JobID} {
				if id == "" {
					continue
				}
				tag, err := tx.Exec(ctx, `INSERT INTO telemetry_admissions(organisation_id,project_id,identity_kind,identity_id,from_at,to_at)
     SELECT organisation_id,$2,$3,$4,from_at,to_at FROM organisation_entitlements
     WHERE organisation_id=$1 AND active AND from_at<=$5 AND to_at>$5
     ON CONFLICT(organisation_id,project_id,identity_kind,identity_id,from_at) DO UPDATE SET to_at=excluded.to_at`, org, project, kind, id, now)
				if err != nil {
					return err
				}
				if tag.RowsAffected() != 1 {
					return store.ErrNotFound
				}
			}
		}
		return nil
	})
}

// The plan limits, each read with the count it caps. A count takes the organisation as $1.
const (
	maxProjectsQuery = "SELECT max_projects FROM organisation_entitlements WHERE organisation_id=$1"
	projectsQuery    = "SELECT count(*) FROM projects WHERE organisation_id=$1"
	maxMembersQuery  = "SELECT max_members FROM organisation_entitlements WHERE organisation_id=$1"
	// Members and the open invitations that would make more, except one for $2 that this invitation
	// replaces.
	seatsQuery = `SELECT (SELECT count(*) FROM memberships WHERE organisation_id=$1) +
        (SELECT count(*) FROM invitations WHERE organisation_id=$1 AND kind='invite' AND email<>$2
          AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at>now())`
)

// planLimit refuses one more of what count counts when the organisation's plan caps it at that.
// It runs after the organisation's row lock, so parallel creates queue and cannot both pass.
// Without a plan, or with a null limit, nothing is refused.
func planLimit(ctx context.Context, tx pgx.Tx, org, limit, count string, args ...any) error {
	var maxN *int32
	err := tx.QueryRow(ctx, limit, org).Scan(&maxN)
	if errors.Is(err, pgx.ErrNoRows) || (err == nil && maxN == nil) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("plan limit: %w", err)
	}
	var n int64
	if err := tx.QueryRow(ctx, count, append([]any{org}, args...)...).Scan(&n); err != nil {
		return fmt.Errorf("plan limit count: %w", err)
	}
	if n >= int64(*maxN) {
		return fmt.Errorf("%d of %d: %w", n, *maxN, store.ErrPlanLimit)
	}
	return nil
}

// RetentionPlans lists the organisations whose plan limits how long call data is kept.
func (s *Store) RetentionPlans(ctx context.Context) ([]store.RetentionPlan, error) {
	rows, err := s.pool.Query(ctx, "SELECT organisation_id, retention_days FROM organisation_entitlements WHERE retention_days IS NOT NULL ORDER BY organisation_id")
	if err != nil {
		return nil, fmt.Errorf("retention plans: %w", err)
	}
	plans, err := pgx.CollectRows(rows, pgx.RowToStructByPos[store.RetentionPlan])
	if err != nil {
		return nil, fmt.Errorf("retention plans: %w", err)
	}
	return plans, nil
}

// TrimCalls deletes an organisation's closed calls that ended before cutoff, with their summaries,
// participants and recording links; an issue keeps its occurrences with no call. Billed usage in
// profiled_call_usage has no call foreign key, so it stays. Open calls are never trimmed.
func (s *Store) TrimCalls(ctx context.Context, org string, cutoff time.Time) (int64, error) {
	var n int64
	err := pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		const old = `SELECT id FROM calls WHERE organisation_id=$1 AND end_state<>'open'
            AND coalesce(ended_at,last_activity_at,first_seen_at)<$2`
		for _, q := range []string{
			"DELETE FROM call_summaries WHERE call_id IN (" + old + ")",
			"DELETE FROM call_participants WHERE call_id IN (" + old + ")",
		} {
			if _, err := tx.Exec(ctx, q, org, cutoff); err != nil {
				return err
			}
		}
		tag, err := tx.Exec(ctx, "DELETE FROM calls WHERE id IN ("+old+")", org, cutoff)
		n = tag.RowsAffected()
		return err
	})
	if err != nil {
		return 0, fmt.Errorf("trim calls of %s: %w", org, err)
	}
	return n, nil
}
