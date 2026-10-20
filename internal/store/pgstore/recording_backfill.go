package pgstore

import (
	"context"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func (s *Store) PendingRecordingBackfills(ctx context.Context) ([]store.RecordingBackfill, error) {
	if err := s.q.InitRecordingBackfills(ctx); err != nil {
		return nil, err
	}
	rows, err := s.q.PendingRecordingBackfills(ctx)
	if err != nil {
		return nil, err
	}
	out := make([]store.RecordingBackfill, 0, len(rows))
	for _, row := range rows {
		out = append(out, store.RecordingBackfill{OrganisationID: row.OrganisationID, ProjectID: row.ProjectID, CursorNS: row.CursorNs, CursorKey: row.CursorKey, Revision: row.Revision})
	}
	return out, nil
}

func (s *Store) AdvanceRecordingBackfill(ctx context.Context, b store.RecordingBackfill, ns int64, key string, done bool) error {
	_, err := s.q.AdvanceRecordingBackfill(ctx, AdvanceRecordingBackfillParams{OrganisationID: b.OrganisationID, ProjectID: b.ProjectID, ExpectedRevision: b.Revision, CursorNs: ns, CursorKey: key, Done: done})
	return err
}

func (s *Store) DeferRecordingBackfill(ctx context.Context, b store.RecordingBackfill) error {
	return s.q.DeferRecordingBackfill(ctx, DeferRecordingBackfillParams{OrganisationID: b.OrganisationID, ProjectID: b.ProjectID, ExpectedRevision: b.Revision})
}
