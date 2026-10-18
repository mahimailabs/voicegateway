package pgstore

import (
	"context"
	"crypto/rand"
	"errors"
	"fmt"
	"net/url"
	"strings"

	"github.com/jackc/pgx/v5/pgconn"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// ErrBadEndpointURL means a worker metrics URL is not an absolute http or https URL.
var ErrBadEndpointURL = errors.New("the worker metrics URL must be an absolute http:// or https:// URL")

// AddWorkerEndpoint lists a self-hosted worker's Prometheus metrics URL for a project. The URL is
// sealed before it is written; only its scheme and host are kept in the clear. A URL the project
// already lists is refused with store.ErrEndpointExists, an unknown project with store.ErrNotFound.
func (s *Store) AddWorkerEndpoint(ctx context.Context, organisationID, projectID, rawURL string) (store.WorkerEndpoint, error) {
	rawURL = strings.TrimSpace(rawURL)
	host, err := endpointHost(rawURL)
	if err != nil {
		return store.WorkerEndpoint{}, err
	}
	listed, err := s.OpenWorkerEndpoints(ctx, organisationID, projectID)
	if err != nil {
		return store.WorkerEndpoint{}, err
	}
	for _, e := range listed {
		if e.URL == rawURL {
			return store.WorkerEndpoint{}, fmt.Errorf("%w: %s", store.ErrEndpointExists, e.ID)
		}
	}
	id := endpointID()
	sealed, err := s.sealer.Seal([]byte(rawURL), endpointSealContext(organisationID, projectID, id))
	if err != nil {
		return store.WorkerEndpoint{}, fmt.Errorf("seal worker metrics URL: %w", err)
	}
	row, err := s.q.CreateWorkerEndpoint(ctx, CreateWorkerEndpointParams{
		OrganisationID: organisationID, ProjectID: projectID, ID: id, Host: host, UrlSealed: sealed,
	})
	if err != nil {
		var pgErr *pgconn.PgError
		if errors.As(err, &pgErr) && pgErr.Code == foreignKeyViolation {
			return store.WorkerEndpoint{}, fmt.Errorf("project %s/%s: %w", organisationID, projectID, store.ErrNotFound)
		}
		return store.WorkerEndpoint{}, fmt.Errorf("add worker endpoint to %s/%s: %w", organisationID, projectID, err)
	}
	return store.WorkerEndpoint{
		OrganisationID: row.OrganisationID, ProjectID: row.ProjectID, ID: row.ID, Host: row.Host, CreatedAt: row.CreatedAt.Time,
	}, nil
}

// RemoveWorkerEndpoint drops one listed endpoint, or answers store.ErrNotFound. Its stored samples
// stay until they expire.
func (s *Store) RemoveWorkerEndpoint(ctx context.Context, organisationID, projectID, id string) error {
	n, err := s.q.DeleteWorkerEndpoint(ctx, DeleteWorkerEndpointParams{OrganisationID: organisationID, ProjectID: projectID, ID: id})
	if err != nil {
		return fmt.Errorf("remove worker endpoint %s: %w", id, err)
	}
	if n == 0 {
		return fmt.Errorf("worker endpoint %s in %s/%s: %w", id, organisationID, projectID, store.ErrNotFound)
	}
	return nil
}

// WorkerEndpoints lists a project's endpoints by id and host, oldest first, without their URLs.
func (s *Store) WorkerEndpoints(ctx context.Context, organisationID, projectID string) ([]store.WorkerEndpoint, error) {
	rows, err := s.q.WorkerEndpoints(ctx, WorkerEndpointsParams{OrganisationID: organisationID, ProjectID: projectID})
	if err != nil {
		return nil, fmt.Errorf("worker endpoints of %s/%s: %w", organisationID, projectID, err)
	}
	out := make([]store.WorkerEndpoint, 0, len(rows))
	for _, r := range rows {
		out = append(out, store.WorkerEndpoint{
			OrganisationID: r.OrganisationID, ProjectID: r.ProjectID, ID: r.ID, Host: r.Host, CreatedAt: r.CreatedAt.Time,
		})
	}
	return out, nil
}

// OpenWorkerEndpoints lists a project's endpoints with their URLs opened, for the worker's read.
func (s *Store) OpenWorkerEndpoints(ctx context.Context, organisationID, projectID string) ([]store.OpenWorkerEndpoint, error) {
	rows, err := s.q.WorkerEndpoints(ctx, WorkerEndpointsParams{OrganisationID: organisationID, ProjectID: projectID})
	if err != nil {
		return nil, fmt.Errorf("worker endpoints of %s/%s: %w", organisationID, projectID, err)
	}
	out := make([]store.OpenWorkerEndpoint, 0, len(rows))
	for _, r := range rows {
		plain, err := s.sealer.Open(r.UrlSealed, endpointSealContext(r.OrganisationID, r.ProjectID, r.ID))
		if err != nil {
			return nil, fmt.Errorf("worker endpoint %s: %w", r.ID, err)
		}
		out = append(out, store.OpenWorkerEndpoint{
			WorkerEndpoint: store.WorkerEndpoint{
				OrganisationID: r.OrganisationID, ProjectID: r.ProjectID, ID: r.ID, Host: r.Host, CreatedAt: r.CreatedAt.Time,
			},
			URL: string(plain),
		})
	}
	return out, nil
}

// endpointHost checks a worker metrics URL and returns its scheme and host, the only part of it kept
// in the clear.
func endpointHost(raw string) (string, error) {
	u, err := url.Parse(raw)
	if err != nil {
		return "", ErrBadEndpointURL // the parse error repeats the URL, which may carry a credential
	}
	scheme := strings.ToLower(u.Scheme)
	if (scheme != "http" && scheme != "https") || u.Host == "" {
		return "", ErrBadEndpointURL
	}
	return scheme + "://" + strings.ToLower(u.Host), nil
}

// endpointID makes an endpoint id such as we_k3j9x2a7b2c4: 12 characters of crypto/rand.Text,
// lowercased (60 random bits).
func endpointID() string {
	return "we_" + strings.ToLower(rand.Text()[:12])
}

// endpointSealContext binds a sealed URL to its row.
func endpointSealContext(organisationID, projectID, id string) string {
	return "worker-endpoint:" + organisationID + "/" + projectID + "/" + id
}
