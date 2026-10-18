package pgstore

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// StatsCalls reads what getCallStats totals: every servable call of a project that started in
// [from, to), oldest first, with when the project was connected and the Postgres clock. Both reads
// run in one REPEATABLE READ READ ONLY transaction, so the figures agree. An unknown project is
// store.ErrNotFound.
func (s *Store) StatsCalls(ctx context.Context, organisationID, projectID string, from, to time.Time) (store.StatsRead, error) {
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return store.StatsRead{}, fmt.Errorf("call stats: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	p, err := q.ProjectConnected(ctx, ProjectConnectedParams{OrganisationID: organisationID, ProjectID: projectID})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.StatsRead{}, store.ErrNotFound
	}
	if err != nil {
		return store.StatsRead{}, fmt.Errorf("call stats: %w", err)
	}
	rows, err := q.StatsCalls(ctx, StatsCallsParams{
		OrganisationID: organisationID, ProjectID: projectID, FromAt: timestamp(&from), ToAt: timestamp(&to),
	})
	if err != nil {
		return store.StatsRead{}, fmt.Errorf("call stats: %w", err)
	}
	out := store.StatsRead{Connected: p.CreatedAt.Time.UTC(), Now: p.Now.Time.UTC(), Calls: make([]store.StatsCall, 0, len(rows))}
	for _, r := range rows {
		c := store.StatsCall{
			ID: r.ID.String(), Room: r.RoomName.String, StartAt: r.StartAt.Time.UTC(), Channel: r.Channel,
			AnswerMs: r.AnswerMs, Models: r.Models,
		}
		if r.AgentKey.Valid {
			k := r.AgentKey.String
			c.AgentKey = &k
		}
		out.Calls = append(out.Calls, c)
	}
	if err := tx.Commit(ctx); err != nil {
		return store.StatsRead{}, fmt.Errorf("call stats: %w", err)
	}
	return out, nil
}
