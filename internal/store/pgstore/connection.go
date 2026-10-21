package pgstore

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// RecordConnection stores one ingestion outcome for a project's source: accepted when reason is
// empty, refused otherwise. It reports whether the project exists; a request addressed to an
// unknown project records nothing.
func (s *Store) RecordConnection(ctx context.Context, org, project, source, reason, usedKey string, at time.Time) (bool, error) {
	ts := pgtype.Timestamptz{Time: at, Valid: true}
	var n int64
	var err error
	if reason == "" {
		n, err = s.q.RecordConnectionAccepted(ctx, RecordConnectionAcceptedParams{OrganisationID: org, ProjectID: project, Source: source, At: ts})
	} else {
		n, err = s.q.RecordConnectionRejected(ctx, RecordConnectionRejectedParams{
			OrganisationID: org, ProjectID: project, Source: source, At: ts,
			Reason: text(reason), UsedKey: text(stripNUL(usedKey)),
		})
	}
	if err != nil {
		return false, fmt.Errorf("record connection: %w", err)
	}
	return n > 0, nil
}

// Connections returns how each source of a project's ingestion last went; a source never seen is
// absent.
func (s *Store) Connections(ctx context.Context, org, project string) ([]store.Connection, error) {
	rows, err := s.q.ProjectConnections(ctx, ProjectConnectionsParams{OrganisationID: org, ProjectID: project})
	if err != nil {
		return nil, fmt.Errorf("read connections: %w", err)
	}
	out := make([]store.Connection, 0, len(rows))
	for _, r := range rows {
		out = append(out, store.Connection{
			Source: r.Source, LastAcceptedAt: timeOf(r.LastAcceptedAt), LastRejectedAt: timeOf(r.LastRejectedAt),
			Reason: strOf(r.Reason), UsedKey: strOf(r.UsedKey),
		})
	}
	return out, nil
}

// FirstCall returns the earliest call of a project, or store.ErrNotFound before the first one.
func (s *Store) FirstCall(ctx context.Context, org, project string) (store.FirstCall, error) {
	row, err := s.q.FirstCall(ctx, FirstCallParams{OrganisationID: org, ProjectID: project})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.FirstCall{}, store.ErrNotFound
	}
	if err != nil {
		return store.FirstCall{}, fmt.Errorf("read first call: %w", err)
	}
	return store.FirstCall{ID: row.ID.String(), StartedAt: timeOf(row.StartedAt)}, nil
}
