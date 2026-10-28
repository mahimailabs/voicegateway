package ingest

import (
	"context"
	"crypto/subtle"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// ScopedProjects selects a project before authenticating the LiveKit token. A key
// string supplied by another tenant never selects the destination of an upload.
type ScopedProjects struct {
	Store interface {
		Project(context.Context, string, string) (store.Project, error)
		LiveKitSecret(context.Context, string, string) (string, error)
	}
	OrganisationID string
	ProjectID      string
}

func (s ScopedProjects) ProjectByLiveKitKey(ctx context.Context, key string) (store.Project, error) {
	if s.OrganisationID == "" || s.ProjectID == "" {
		return store.Project{}, store.ErrNotFound
	}
	p, err := s.Store.Project(ctx, s.OrganisationID, s.ProjectID)
	if err != nil {
		return store.Project{}, err
	}
	if p.OrganisationID != s.OrganisationID || p.ID != s.ProjectID || subtle.ConstantTimeCompare([]byte(p.LiveKitAPIKey), []byte(key)) != 1 {
		return store.Project{}, store.ErrNotFound
	}
	return p, nil
}

func (s ScopedProjects) LiveKitSecret(ctx context.Context, org, project string) (string, error) {
	if org != s.OrganisationID || project != s.ProjectID {
		return "", store.ErrNotFound
	}
	return s.Store.LiveKitSecret(ctx, org, project)
}
