package pgstore

import (
	"context"
	"errors"
	"fmt"

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
            CASE WHEN started_at IS NOT NULL THEN greatest(0,extract(epoch FROM
              (CASE end_state WHEN 'observed' THEN ended_at WHEN 'inferred' THEN last_activity_at END)-started_at)) ELSE 0 END
            FROM calls WHERE organisation_id=$1 ON CONFLICT(call_id) DO NOTHING`, org)
		return err
	})
}

// EntitlementUsage reads one snapshot of the entitlement and its durable measured usage.
func (s *Store) EntitlementUsage(ctx context.Context, org string) (store.EntitlementUsage, error) {
	var u store.EntitlementUsage
	err := s.pool.QueryRow(ctx, `SELECT e.from_at,e.to_at,e.limit_minutes,e.active,e.revision,
        coalesce((SELECT floor(sum(seconds))::bigint FROM profiled_call_usage u
            WHERE u.organisation_id=e.organisation_id AND u.effective_end>=e.from_at AND u.effective_end<e.to_at),0)
        FROM organisation_entitlements e WHERE e.organisation_id=$1`, org).
		Scan(&u.From, &u.To, &u.LimitMinutes, &u.Active, &u.Revision, &u.CallSeconds)
	if errors.Is(err, pgx.ErrNoRows) {
		return u, store.ErrNotFound
	}
	if err != nil {
		return u, fmt.Errorf("entitlement usage: %w", err)
	}
	return u, nil
}
