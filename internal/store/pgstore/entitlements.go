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
		err := tx.QueryRow(ctx, "SELECT from_at,to_at,limit_minutes,active,revision FROM organisation_entitlements WHERE organisation_id=$1", org).Scan(&old.From, &old.To, &old.LimitMinutes, &old.Active, &old.Revision)
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return err
		}
		if err == nil && old.Revision >= e.Revision {
			if old.Revision == e.Revision && old.From.Equal(e.From) && old.To.Equal(e.To) && old.LimitMinutes == e.LimitMinutes && old.Active == e.Active {
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
		if _, err := tx.Exec(ctx, `INSERT INTO organisation_entitlements(organisation_id,from_at,to_at,limit_minutes,active,revision)
            VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(organisation_id) DO UPDATE SET from_at=$2,to_at=$3,limit_minutes=$4,active=$5,revision=$6`, org, e.From, e.To, e.LimitMinutes, e.Active, e.Revision); err != nil {
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
	err := s.pool.QueryRow(ctx, `SELECT e.from_at,e.to_at,e.limit_minutes,e.active,e.revision,
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
		Scan(&u.From, &u.To, &u.LimitMinutes, &u.Active, &u.Revision, &u.CallSeconds, &u.UnmeasuredCalls, &u.OpenCalls)
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
