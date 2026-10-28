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
	var bucketSecret []byte
	if p.Bucket != nil {
		if bucketSecret, err = s.sealer.Seal([]byte(p.Bucket.SecretKey), bucketSealContext(p.OrganisationID, p.ID)); err != nil {
			return store.Project{}, fmt.Errorf("seal bucket secret: %w", err)
		}
	}
	// The project and its bucket are written together, so a project never exists without the bucket
	// it was created with.
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return store.Project{}, fmt.Errorf("create project %s/%s: %w", p.OrganisationID, p.ID, err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	row, err := q.CreateProject(ctx, CreateProjectParams{
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
	if b := p.Bucket; b != nil {
		if err := q.InsertProjectBucket(ctx, InsertProjectBucketParams{
			OrganisationID: p.OrganisationID, ProjectID: p.ID, Endpoint: b.Endpoint, UseSsl: b.UseSSL,
			Bucket: b.Bucket, Region: b.Region, AccessKey: b.AccessKey, SecretKeySealed: bucketSecret,
		}); err != nil {
			return store.Project{}, fmt.Errorf("create project %s/%s bucket: %w", p.OrganisationID, p.ID, err)
		}
	}
	if err := tx.Commit(ctx); err != nil {
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

// bucketSealContext binds a sealed bucket secret to its project, apart from its LiveKit secret.
func bucketSealContext(organisationID, projectID string) string {
	return "project-bucket:" + organisationID + "/" + projectID
}

// ProjectBucket returns the bucket a project brought for its recordings, its secret opened, or
// store.ErrNotFound when the project uses the instance's bucket.
func (s *Store) ProjectBucket(ctx context.Context, organisationID, projectID string) (store.ProjectBucket, error) {
	row, err := s.q.ProjectBucket(ctx, ProjectBucketParams{OrganisationID: organisationID, ProjectID: projectID})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.ProjectBucket{}, store.ErrNotFound
	}
	if err != nil {
		return store.ProjectBucket{}, fmt.Errorf("bucket of %s/%s: %w", organisationID, projectID, err)
	}
	return s.openBucket(ProjectBucketRow(row))
}

// ProjectBuckets lists every bucket projects brought, their secrets opened, for retention.
func (s *Store) ProjectBuckets(ctx context.Context) ([]store.ProjectBucket, error) {
	rows, err := s.q.ListProjectBuckets(ctx)
	if err != nil {
		return nil, fmt.Errorf("list project buckets: %w", err)
	}
	out := make([]store.ProjectBucket, 0, len(rows))
	for _, row := range rows {
		b, err := s.openBucket(ProjectBucketRow(row))
		if err != nil {
			return nil, err
		}
		out = append(out, b)
	}
	return out, nil
}

func (s *Store) openBucket(row ProjectBucketRow) (store.ProjectBucket, error) {
	plain, err := s.sealer.Open(row.SecretKeySealed, bucketSealContext(row.OrganisationID, row.ProjectID))
	if err != nil {
		return store.ProjectBucket{}, fmt.Errorf("bucket secret of %s/%s: %w", row.OrganisationID, row.ProjectID, err)
	}
	return store.ProjectBucket{
		OrganisationID: row.OrganisationID, ProjectID: row.ProjectID, Endpoint: row.Endpoint, UseSSL: row.UseSsl,
		Bucket: row.Bucket, Region: row.Region, AccessKey: row.AccessKey, SecretKey: string(plain),
	}, nil
}

// UpdateLiveKitCredentials replaces a project's LiveKit key and secret, the secret sealed in the
// project's own scope as at create. An unknown project is store.ErrNotFound.
func (s *Store) UpdateLiveKitCredentials(ctx context.Context, organisationID, projectID, apiKey, apiSecret string) error {
	sealed, err := s.sealer.Seal([]byte(apiSecret), sealContext(organisationID, projectID))
	if err != nil {
		return fmt.Errorf("seal LiveKit secret: %w", err)
	}
	n, err := s.q.UpdateLiveKitCredentials(ctx, UpdateLiveKitCredentialsParams{
		OrganisationID: organisationID, ID: projectID, LivekitApiKey: apiKey, LivekitApiSecretSealed: sealed,
	})
	if err != nil {
		return fmt.Errorf("update LiveKit credentials: %w", err)
	}
	if n == 0 {
		return store.ErrNotFound
	}
	return nil
}
