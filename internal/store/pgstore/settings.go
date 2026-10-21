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
	return store.ProjectSettings{Thresholds: store.Thresholds{AnswerMs: row.AnswerMs, JoinMs: row.JoinMs, TTSStartMs: row.TtsStartMs}, Revision: row.Revision, AllowedOrigins: row.AllowedOrigins, AllowedOriginsRevision: row.AllowedOriginsRevision}, nil
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
		result = store.ProjectSettings{Thresholds: store.Thresholds{AnswerMs: row.AnswerMs, JoinMs: row.JoinMs, TTSStartMs: row.TtsStartMs}, Revision: row.Revision, AllowedOrigins: row.AllowedOrigins, AllowedOriginsRevision: row.AllowedOriginsRevision}
		return nil
	})
	if err != nil {
		return store.ProjectSettings{}, fmt.Errorf("update project settings: %w", err)
	}
	return result, nil
}

// UpdateAllowedOrigins replaces the project's allowed embed origins if its settings are still at
// revision; nil or empty clears them, which allows none. The caller normalises the origins.
func (s *Store) UpdateAllowedOrigins(ctx context.Context, org, project string, revision int64, origins []string) (store.ProjectSettings, error) {
	if revision < 0 || revision >= 9007199254740991 {
		return store.ProjectSettings{}, store.ErrInvalidSettingsRevision
	}
	if len(origins) == 0 {
		origins = nil
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
		row, err := q.UpdateAllowedOrigins(ctx, UpdateAllowedOriginsParams{OrganisationID: org, ProjectID: project, ExpectedRevision: revision, AllowedOrigins: origins})
		if errors.Is(err, pgx.ErrNoRows) {
			return store.ErrSettingsConflict
		}
		if err != nil {
			return err
		}
		result = store.ProjectSettings{Thresholds: store.Thresholds{AnswerMs: row.AnswerMs, JoinMs: row.JoinMs, TTSStartMs: row.TtsStartMs}, Revision: row.Revision, AllowedOrigins: row.AllowedOrigins, AllowedOriginsRevision: row.AllowedOriginsRevision}
		return nil
	})
	if err != nil {
		return store.ProjectSettings{}, fmt.Errorf("update allowed origins: %w", err)
	}
	return result, nil
}

// EmbedGrantActive reports whether an embed token's grant still holds (auth.Users).
func (s *Store) EmbedGrantActive(ctx context.Context, g store.EmbedScope) (bool, error) {
	active, err := s.q.EmbedGrantActive(ctx, EmbedGrantActiveParams{OrganisationID: g.OrganisationID, ProjectID: g.ProjectID, Origin: g.Origin, KeyID: g.KeyID, UserID: g.UserID, Content: g.Content})
	if err != nil {
		return false, fmt.Errorf("embed grant: %w", err)
	}
	return active, nil
}
