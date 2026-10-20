package pgstore

import (
	"context"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func (s *Store) ProjectSettings(ctx context.Context, org, project string) (store.ProjectSettings, error) {
	row, err := s.q.ReadProjectSettings(ctx, ReadProjectSettingsParams{OrganisationID: org, ProjectID: project})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.ProjectSettings{}, store.ErrNotFound
	}
	if err != nil {
		return store.ProjectSettings{}, fmt.Errorf("read project settings: %w", err)
	}
	return store.ProjectSettings{Thresholds: store.Thresholds{AnswerMs: row.AnswerMs, JoinMs: row.JoinMs, TTSStartMs: row.TtsStartMs}, Revision: row.Revision}, nil
}

// UpdateProjectSettings atomically compares the read revision and replaces all thresholds.
// A project key-share lock prevents deletion between initialization and update; concurrent
// first writes serialize on the settings row and still compare their original revision.
func (s *Store) UpdateProjectSettings(ctx context.Context, org, project string, revision int64, thresholds store.Thresholds) (store.ProjectSettings, error) {
	if err := thresholds.Validate(); err != nil {
		return store.ProjectSettings{}, err
	}
	if revision < 0 || revision >= 9007199254740991 {
		return store.ProjectSettings{}, store.ErrInvalidSettingsRevision
	}
	var result store.ProjectSettings
	err := pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		q := s.q.WithTx(tx)
		if _, err := q.LockSettingsProject(ctx, LockSettingsProjectParams{OrganisationID: org, ProjectID: project}); err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				return store.ErrNotFound
			}
			return err
		}
		if err := q.EnsureProjectSettings(ctx, EnsureProjectSettingsParams{OrganisationID: org, ProjectID: project}); err != nil {
			return err
		}
		row, err := q.UpdateProjectSettings(ctx, UpdateProjectSettingsParams{OrganisationID: org, ProjectID: project, ExpectedRevision: revision, AnswerMs: thresholds.AnswerMs, JoinMs: thresholds.JoinMs, TtsStartMs: thresholds.TTSStartMs})
		if errors.Is(err, pgx.ErrNoRows) {
			return store.ErrSettingsConflict
		}
		if err != nil {
			return err
		}
		result = store.ProjectSettings{Thresholds: store.Thresholds{AnswerMs: row.AnswerMs, JoinMs: row.JoinMs, TTSStartMs: row.TtsStartMs}, Revision: row.Revision}
		return nil
	})
	if err != nil {
		return store.ProjectSettings{}, fmt.Errorf("update project settings: %w", err)
	}
	return result, nil
}
