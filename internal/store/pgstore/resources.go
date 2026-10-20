package pgstore

import (
	"context"
	"crypto/rand"
	"errors"
	"fmt"
	"strings"

	"github.com/jackc/pgx/v5/pgconn"

	"github.com/mahimailabs/voicegateway/internal/store"
)

var (
	ErrBadResourceTarget    = errors.New("resource target requires kind sfu or worker, a target name, and an exact non-root container id")
	ErrResourceTargetExists = errors.New("the project already maps this kind and target; remove it first")
)

func (s *Store) AddResourceEndpoint(ctx context.Context, org, project, rawURL, kind, target, containerID string) (store.ResourceEndpoint, error) {
	rawURL = strings.TrimSpace(rawURL)
	host, err := endpointHost(rawURL)
	if err != nil {
		return store.ResourceEndpoint{}, err
	}
	if (kind != "sfu" && kind != "worker") || len(target) < 1 || len(target) > 256 || strings.TrimSpace(target) != target || len(containerID) < 2 || len(containerID) > 1024 || !strings.HasPrefix(containerID, "/") {
		return store.ResourceEndpoint{}, ErrBadResourceTarget
	}
	id := "re_" + strings.ToLower(rand.Text()[:12])
	sealed, err := s.sealer.Seal([]byte(rawURL), resourceSealContext(org, project, id))
	if err != nil {
		return store.ResourceEndpoint{}, fmt.Errorf("seal resource endpoint: %w", err)
	}
	row, err := s.q.CreateResourceEndpoint(ctx, CreateResourceEndpointParams{OrganisationID: org, ProjectID: project, ID: id, Host: host, UrlSealed: sealed, Kind: kind, Target: target, ContainerID: containerID})
	if err != nil {
		var pgErr *pgconn.PgError
		if errors.As(err, &pgErr) {
			if pgErr.Code == foreignKeyViolation {
				return store.ResourceEndpoint{}, store.ErrNotFound
			}
			if pgErr.Code == "23505" {
				return store.ResourceEndpoint{}, ErrResourceTargetExists
			}
		}
		return store.ResourceEndpoint{}, fmt.Errorf("store resource endpoint: %w", err)
	}
	return resourceEndpoint(row), nil
}

func (s *Store) ResourceEndpoints(ctx context.Context, org, project string) ([]store.ResourceEndpoint, error) {
	rows, err := s.q.ResourceEndpoints(ctx, ResourceEndpointsParams{OrganisationID: org, ProjectID: project})
	if err != nil {
		return nil, fmt.Errorf("list resource endpoints: %w", err)
	}
	out := make([]store.ResourceEndpoint, 0, len(rows))
	for _, row := range rows {
		out = append(out, resourceEndpoint(row))
	}
	return out, nil
}

func (s *Store) OpenResourceEndpoints(ctx context.Context, org, project string) ([]store.OpenResourceEndpoint, error) {
	rows, err := s.q.ResourceEndpoints(ctx, ResourceEndpointsParams{OrganisationID: org, ProjectID: project})
	if err != nil {
		return nil, fmt.Errorf("list resource endpoints: %w", err)
	}
	out := make([]store.OpenResourceEndpoint, 0, len(rows))
	for _, row := range rows {
		plain, err := s.sealer.Open(row.UrlSealed, resourceSealContext(org, project, row.ID))
		if err != nil {
			return nil, fmt.Errorf("open resource endpoint: %w", err)
		}
		out = append(out, store.OpenResourceEndpoint{ResourceEndpoint: resourceEndpoint(row), URL: string(plain)})
	}
	return out, nil
}

func (s *Store) RemoveResourceEndpoint(ctx context.Context, org, project, id string) error {
	n, err := s.q.DeleteResourceEndpoint(ctx, DeleteResourceEndpointParams{OrganisationID: org, ProjectID: project, ID: id})
	if err != nil {
		return fmt.Errorf("delete resource endpoint: %w", err)
	}
	if n == 0 {
		return store.ErrNotFound
	}
	return nil
}

func resourceEndpoint(row ProjectResourceEndpoint) store.ResourceEndpoint {
	return store.ResourceEndpoint{OrganisationID: row.OrganisationID, ProjectID: row.ProjectID, ID: row.ID, Host: row.Host, Kind: row.Kind, Target: row.Target, ContainerID: row.ContainerID, CreatedAt: row.CreatedAt.Time}
}

func resourceSealContext(org, project, id string) string {
	return "resource-endpoint:" + org + "/" + project + "/" + id
}
