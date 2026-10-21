package pgstore

import (
	"context"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func (s *Store) PendingMediaBackfills(ctx context.Context) ([]store.MediaBackfill, error) {
	if err := s.q.InitMediaBackfills(ctx); err != nil {
		return nil, err
	}
	rows, err := s.q.PendingMediaBackfills(ctx)
	if err != nil {
		return nil, err
	}
	out := make([]store.MediaBackfill, 0, len(rows))
	for _, row := range rows {
		out = append(out, store.MediaBackfill{OrganisationID: row.OrganisationID, ProjectID: row.ProjectID, CursorNS: row.CursorNs, CursorKey: row.CursorKey, Revision: row.Revision})
	}
	return out, nil
}

func (s *Store) AdvanceMediaBackfill(ctx context.Context, b store.MediaBackfill, ns int64, key string, done bool) error {
	_, err := s.q.AdvanceMediaBackfill(ctx, AdvanceMediaBackfillParams{OrganisationID: b.OrganisationID, ProjectID: b.ProjectID, ExpectedRevision: b.Revision, CursorNs: ns, CursorKey: key, Done: done})
	return err
}

func (s *Store) DeferMediaBackfill(ctx context.Context, b store.MediaBackfill) error {
	return s.q.DeferMediaBackfill(ctx, DeferMediaBackfillParams{OrganisationID: b.OrganisationID, ProjectID: b.ProjectID, ExpectedRevision: b.Revision})
}
