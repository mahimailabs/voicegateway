package pgstore

import (
	"context"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func (s *Store) CallMetadata(ctx context.Context, org, project, callID string) (store.CallMetadata, error) {
	id, err := uuid(callID)
	if err != nil {
		return store.CallMetadata{}, store.ErrNotFound
	}
	row, err := s.q.ReadCallMetadata(ctx, ReadCallMetadataParams{OrganisationID: org, ProjectID: project, CallID: id})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.CallMetadata{}, store.ErrNotFound
	}
	if err != nil {
		return store.CallMetadata{}, fmt.Errorf("read call metadata: %w", err)
	}
	return store.CallMetadata{Tags: row.Tags, Archived: row.Archived, Revision: row.MetadataRevision}, nil
}

func (s *Store) UpdateCallMetadata(ctx context.Context, org, project, callID string, expected int64, tags []string, archived bool) (store.CallMetadata, error) {
	if expected < 0 || expected >= 9007199254740991 {
		return store.CallMetadata{}, store.ErrInvalidMetadata
	}
	normalized, err := store.NormalizeTags(tags)
	if err != nil {
		return store.CallMetadata{}, err
	}
	id, err := uuid(callID)
	if err != nil {
		return store.CallMetadata{}, store.ErrNotFound
	}
	row, err := s.q.UpdateCallMetadata(ctx, UpdateCallMetadataParams{OrganisationID: org, ProjectID: project, CallID: id, ExpectedRevision: expected, Tags: normalized, Archived: archived})
	if errors.Is(err, pgx.ErrNoRows) {
		if _, readErr := s.CallMetadata(ctx, org, project, callID); readErr != nil {
			return store.CallMetadata{}, readErr
		}
		return store.CallMetadata{}, store.ErrMetadataConflict
	}
	if err != nil {
		return store.CallMetadata{}, fmt.Errorf("update call metadata: %w", err)
	}
	return store.CallMetadata{Tags: row.Tags, Archived: row.Archived, Revision: row.MetadataRevision}, nil
}
