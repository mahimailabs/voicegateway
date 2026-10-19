package pgstore

import (
	"context"
	"errors"
	"fmt"
	"io/fs"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/pressly/goose/v3"

	"github.com/mahimailabs/voicegateway/internal/seal"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/migrations"
)

// uniqueViolation is the Postgres error code for a broken unique constraint.
const uniqueViolation = "23505"

// Store reads and writes configuration in Postgres.
type Store struct {
	pool   *pgxpool.Pool
	q      *Queries
	sealer *seal.Sealer
}

// NewStore returns a Store over an open pool. The sealer seals and opens LiveKit secrets.
func NewStore(pool *pgxpool.Pool, sealer *seal.Sealer) *Store {
	return &Store{pool: pool, q: New(pool), sealer: sealer}
}

// Migrate applies every pending migration in migrations/postgres and returns how many ran.
func Migrate(ctx context.Context, pool *pgxpool.Pool) (int, error) {
	fsys, err := fs.Sub(migrations.Postgres, "postgres")
	if err != nil {
		return 0, fmt.Errorf("migrations: %w", err)
	}
	db := stdlib.OpenDBFromPool(pool)
	defer func() { _ = db.Close() }()
	provider, err := goose.NewProvider(goose.DialectPostgres, db, fsys)
	if err != nil {
		return 0, fmt.Errorf("migrations: %w", err)
	}
	results, err := provider.Up(ctx)
	if err != nil {
		return len(results), fmt.Errorf("migrate postgres: %w", err)
	}
	return len(results), nil
}

// NewProject is what creating a project needs (store.NewProject). The secret is sealed before it
// is written.
type NewProject = store.NewProject

// CreateProject stores a project with its LiveKit secret sealed in its own scope.
func (s *Store) CreateProject(ctx context.Context, p NewProject) (store.Project, error) {
	ok, err := s.q.OrganisationExists(ctx, p.OrganisationID)
	if err != nil {
		return store.Project{}, fmt.Errorf("check organisation %s: %w", p.OrganisationID, err)
	}
	if !ok {
		return store.Project{}, fmt.Errorf("organisation %s: %w", p.OrganisationID, store.ErrNotFound)
	}

	sealed, err := s.sealer.Seal([]byte(p.LiveKitAPISecret), sealContext(p.OrganisationID, p.ID))
	if err != nil {
		return store.Project{}, fmt.Errorf("seal LiveKit secret: %w", err)
	}
	row, err := s.q.CreateProject(ctx, CreateProjectParams{
		OrganisationID:         p.OrganisationID,
		ID:                     p.ID,
		Name:                   p.Name,
		LivekitUrl:             p.LiveKitURL,
		LivekitApiKey:          p.LiveKitAPIKey,
		LivekitApiSecretSealed: sealed,
	})
	if err != nil {
		var pgErr *pgconn.PgError
		if errors.As(err, &pgErr) && pgErr.Code == uniqueViolation {
			if pgErr.ConstraintName == "projects_pkey" {
				return store.Project{}, fmt.Errorf("project %s/%s: %w", p.OrganisationID, p.ID, store.ErrProjectExists)
			}
		}
		return store.Project{}, fmt.Errorf("create project %s/%s: %w", p.OrganisationID, p.ID, err)
	}
	return store.Project{
		OrganisationID: row.OrganisationID, ID: row.ID, Name: row.Name,
		LiveKitURL: row.LivekitUrl, LiveKitAPIKey: row.LivekitApiKey, CreatedAt: row.CreatedAt.Time, Recordings: row.Recordings,
	}, nil
}

// Projects lists every project, the way the worker finds what to read.
func (s *Store) Projects(ctx context.Context) ([]store.Project, error) {
	rows, err := s.q.ListProjects(ctx)
	if err != nil {
		return nil, fmt.Errorf("list projects: %w", err)
	}
	out := make([]store.Project, 0, len(rows))
	for _, row := range rows {
		out = append(out, store.Project{
			OrganisationID: row.OrganisationID, ID: row.ID, Name: row.Name,
			LiveKitURL: row.LivekitUrl, LiveKitAPIKey: row.LivekitApiKey, CreatedAt: row.CreatedAt.Time, Recordings: row.Recordings,
		})
	}
	return out, nil
}

// Project finds a project by its organisation and id, or store.ErrNotFound.
func (s *Store) Project(ctx context.Context, organisationID, projectID string) (store.Project, error) {
	row, err := s.q.ProjectByID(ctx, ProjectByIDParams{OrganisationID: organisationID, ID: projectID})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.Project{}, store.ErrNotFound
	}
	if err != nil {
		return store.Project{}, fmt.Errorf("project %s/%s: %w", organisationID, projectID, err)
	}
	return store.Project{
		OrganisationID: row.OrganisationID, ID: row.ID, Name: row.Name,
		LiveKitURL: row.LivekitUrl, LiveKitAPIKey: row.LivekitApiKey, CreatedAt: row.CreatedAt.Time, Recordings: row.Recordings,
	}, nil
}

// LiveKitSecret opens a project's LiveKit API secret, for verifying what that project signs.
func (s *Store) LiveKitSecret(ctx context.Context, organisationID, projectID string) (string, error) {
	sealed, err := s.q.SealedLiveKitSecret(ctx, SealedLiveKitSecretParams{OrganisationID: organisationID, ID: projectID})
	if errors.Is(err, pgx.ErrNoRows) {
		return "", store.ErrNotFound
	}
	if err != nil {
		return "", fmt.Errorf("LiveKit secret for %s/%s: %w", organisationID, projectID, err)
	}
	plain, err := s.sealer.Open(sealed, sealContext(organisationID, projectID))
	if err != nil {
		return "", fmt.Errorf("LiveKit secret for %s/%s: %w", organisationID, projectID, err)
	}
	return string(plain), nil
}

// sealContext binds a sealed secret to its row.
func sealContext(organisationID, projectID string) string {
	return "project:" + organisationID + "/" + projectID
}
