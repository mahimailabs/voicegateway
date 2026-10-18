package pgstore

import (
	"context"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// CreateProjectKey stores a key's key_id and the SHA-256 of its secret, never the secret. createdBy
// is the creating user's id, empty for the command line. A taken key_id is store.ErrKeyExists; an
// unknown project is store.ErrNotFound.
func (s *Store) CreateProjectKey(ctx context.Context, organisationID, projectID, keyID, name string, secretSHA256 []byte, createdBy string) error {
	var by pgtype.UUID
	if createdBy != "" {
		id, err := uuid(createdBy)
		if err != nil {
			return fmt.Errorf("create project key: %w", err)
		}
		by = id
	}
	err := s.q.CreateProjectKey(ctx, CreateProjectKeyParams{
		KeyID: keyID, OrganisationID: organisationID, ProjectID: projectID, Name: name, SecretSha256: secretSHA256, CreatedBy: by,
	})
	var pgErr *pgconn.PgError
	switch {
	case errors.As(err, &pgErr) && pgErr.Code == uniqueViolation:
		return store.ErrKeyExists
	case errors.As(err, &pgErr) && pgErr.Code == foreignKeyViolation:
		return store.ErrNotFound
	case err != nil:
		return fmt.Errorf("create project key: %w", err)
	}
	return nil
}

// ProjectKey returns the stored key for a key_id, or store.ErrNotFound.
func (s *Store) ProjectKey(ctx context.Context, keyID string) (store.StoredKey, error) {
	r, err := s.q.ProjectKey(ctx, keyID)
	if errors.Is(err, pgx.ErrNoRows) {
		return store.StoredKey{}, store.ErrNotFound
	}
	if err != nil {
		return store.StoredKey{}, fmt.Errorf("project key: %w", err)
	}
	return store.StoredKey{KeyID: r.KeyID, OrganisationID: r.OrganisationID, ProjectID: r.ProjectID, SecretSHA256: r.SecretSha256, Revoked: r.RevokedAt.Valid}, nil
}

// ProjectKeys lists a project's keys, newest first, revoked ones included.
func (s *Store) ProjectKeys(ctx context.Context, organisationID, projectID string) ([]store.ProjectKey, error) {
	rows, err := s.q.ListProjectKeys(ctx, ListProjectKeysParams{OrganisationID: organisationID, ProjectID: projectID})
	if err != nil {
		return nil, fmt.Errorf("project keys: %w", err)
	}
	out := make([]store.ProjectKey, 0, len(rows))
	for _, r := range rows {
		k := store.ProjectKey{KeyID: r.KeyID, Name: r.Name, CreatedAt: r.CreatedAt.Time.UTC(), LastUsedAt: timeOf(r.LastUsedAt), RevokedAt: timeOf(r.RevokedAt)}
		if r.CreatedBy.Valid {
			e := r.CreatedBy.String
			k.CreatedBy = &e
		}
		out = append(out, k)
	}
	return out, nil
}

// RevokeProjectKey revokes one of a project's keys; revoking twice keeps the first time. A key_id
// that is not this project's is store.ErrNotFound.
func (s *Store) RevokeProjectKey(ctx context.Context, organisationID, projectID, keyID string) error {
	n, err := s.q.RevokeProjectKey(ctx, RevokeProjectKeyParams{OrganisationID: organisationID, ProjectID: projectID, KeyID: keyID})
	if err != nil {
		return fmt.Errorf("revoke project key: %w", err)
	}
	if n == 0 {
		return store.ErrNotFound
	}
	return nil
}

// TouchProjectKey records that a key was used, at most once a minute.
func (s *Store) TouchProjectKey(ctx context.Context, keyID string) error {
	if err := s.q.TouchProjectKey(ctx, keyID); err != nil {
		return fmt.Errorf("touch project key: %w", err)
	}
	return nil
}

// KeyWorkspace is what getWorkspaces shows a project key: its organisation with its one project.
func (s *Store) KeyWorkspace(ctx context.Context, organisationID, projectID string) (store.Workspace, error) {
	r, err := s.q.KeyWorkspace(ctx, KeyWorkspaceParams{OrganisationID: organisationID, ProjectID: projectID})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.Workspace{}, store.ErrNotFound
	}
	if err != nil {
		return store.Workspace{}, fmt.Errorf("key workspace: %w", err)
	}
	return store.Workspace{ID: r.OrganisationID, Name: r.OrganisationName, Projects: []store.WorkspaceProject{{ID: r.ProjectID, Name: r.ProjectName}}}, nil
}
