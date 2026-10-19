package app

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strings"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/mahimailabs/voicegateway/internal/api"
	"github.com/mahimailabs/voicegateway/internal/auth"
	"github.com/mahimailabs/voicegateway/internal/config"
	"github.com/mahimailabs/voicegateway/internal/seal"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/store/chstore"
	"github.com/mahimailabs/voicegateway/internal/store/pgstore"
)

// Migrate applies every pending Postgres and ClickHouse migration.
func Migrate(ctx context.Context, c config.Config, log *slog.Logger) error {
	pool, err := pgxpool.New(ctx, c.PostgresURL)
	if err != nil {
		return fmt.Errorf("postgres: %w", redactErr(err, c.PostgresURL))
	}
	defer pool.Close()
	n, err := pgstore.Migrate(ctx, pool)
	if err != nil {
		return err
	}
	log.Info("postgres migrated", "applied", n)

	n, err = chstore.Migrate(ctx, c.ClickHouseURL)
	if err != nil {
		return redactErr(err, c.ClickHouseURL)
	}
	log.Info("clickhouse migrated", "applied", n)
	return nil
}

// ProjectInput is what `voicegateway project add` collects.
type ProjectInput struct {
	OrganisationID   string
	ID               string // derived from Name when empty
	Name             string
	LiveKitURL       string
	LiveKitAPIKey    string
	LiveKitAPISecret string
}

// projectFlags names each field of a new project as voicegateway project add asks for it.
var projectFlags = map[string]string{
	api.FieldName: "--name", api.FieldID: "--id", api.FieldLiveKitURL: "--livekit-url",
	api.FieldLiveKitAPIKey: "--livekit-api-key", api.FieldLiveKitAPISecret: "LIVEKIT_API_SECRET",
}

// AddProject creates a project with its LiveKit secret sealed under the configured key. The fields
// are checked by api.CheckProject, the rule createProject applies too, and the id defaults to the
// name's api.Slug.
func AddProject(ctx context.Context, c config.Config, in ProjectInput) (store.Project, error) {
	p, err := api.CheckProject(store.NewProject{
		OrganisationID: orgOrDefault(in.OrganisationID), ID: in.ID, Name: in.Name,
		LiveKitURL: in.LiveKitURL, LiveKitAPIKey: in.LiveKitAPIKey, LiveKitAPISecret: in.LiveKitAPISecret,
	})
	var invalid *api.ProjectInputError
	if errors.As(err, &invalid) {
		return store.Project{}, errors.New(invalid.Describe(projectFlags))
	}
	if err != nil {
		return store.Project{}, err
	}

	sealer, err := seal.New(c.SecretKey)
	if err != nil {
		return store.Project{}, err
	}
	pool, err := pgxpool.New(ctx, c.PostgresURL)
	if err != nil {
		return store.Project{}, fmt.Errorf("postgres: %w", redactErr(err, c.PostgresURL))
	}
	defer pool.Close()
	return pgstore.NewStore(pool, sealer).CreateProject(ctx, p)
}

// AddUser gives an email a role in one organisation: admin for the first admin of an install
// (decision 0027), or member or viewer (phase 06b). A new email gets an account, with the password
// password returns; an email that has one gets a membership, and password is not called. A user
// already in the organisation is store.ErrMemberExists, and nothing changes. It reports whether
// an account was created.
func AddUser(ctx context.Context, c config.Config, email, organisationID, role string, password func() (string, error)) (bool, error) {
	email = strings.TrimSpace(email)
	if email == "" || !strings.Contains(email, "@") {
		return false, errors.New("--email must be an email address")
	}
	if role != "admin" && role != "member" && role != "viewer" {
		return false, errors.New("--role must be admin, member or viewer")
	}
	if organisationID == "" {
		organisationID = "default"
	}
	pool, err := pgxpool.New(ctx, c.PostgresURL)
	if err != nil {
		return false, fmt.Errorf("postgres: %w", redactErr(err, c.PostgresURL))
	}
	defer pool.Close()
	pg := pgstore.NewStore(pool, nil)
	if _, err := pg.UserByEmail(ctx, email); err == nil {
		_, err := pg.AddMembership(ctx, email, organisationID, role)
		return false, err
	} else if !errors.Is(err, store.ErrNotFound) {
		return false, err
	}
	hash, err := newPasswordHash(ctx, password)
	if err != nil {
		return false, err
	}
	if _, err := pg.AddUser(ctx, email, hash, organisationID, role); err != nil {
		return false, err
	}
	return true, nil
}

// ResetPassword sets a new password for an account and signs it out of every session (phase 06b):
// decision 0027's reset by an admin, and the way back in for a lone admin. An unknown email is
// store.ErrNotFound.
func ResetPassword(ctx context.Context, c config.Config, email string, password func() (string, error)) error {
	hash, err := newPasswordHash(ctx, password)
	if err != nil {
		return err
	}
	pool, err := pgxpool.New(ctx, c.PostgresURL)
	if err != nil {
		return fmt.Errorf("postgres: %w", redactErr(err, c.PostgresURL))
	}
	defer pool.Close()
	return pgstore.NewStore(pool, nil).SetPassword(ctx, strings.TrimSpace(email), hash)
}

// newPasswordHash reads a password and hashes it, refusing one shorter than minPassword.
func newPasswordHash(ctx context.Context, password func() (string, error)) (string, error) {
	pw, err := password()
	if err != nil {
		return "", err
	}
	if len(pw) < minPassword {
		return "", fmt.Errorf("the password must be at least %d characters", minPassword)
	}
	return auth.HashPassword(ctx, pw)
}

// minPassword is the shortest password accepted.
const minPassword = 12

// CreateKey creates an API key for a project from the command line (phase 06c) and returns the full
// key, the only time it is shown; the store keeps its SHA-256. A drawn key_id that is already taken
// is drawn again, once. An unknown project is store.ErrNotFound.
func CreateKey(ctx context.Context, c config.Config, organisationID, projectID, name string) (string, error) {
	name, err := api.KeyName(name)
	if err != nil {
		return "", errors.New("--name must be 1 to 64 characters, with no control characters")
	}
	pool, err := pgxpool.New(ctx, c.PostgresURL)
	if err != nil {
		return "", fmt.Errorf("postgres: %w", redactErr(err, c.PostgresURL))
	}
	defer pool.Close()
	pg := pgstore.NewStore(pool, nil)
	for attempt := 0; ; attempt++ {
		keyID, sum, full, err := auth.NewKey()
		if err != nil {
			return "", err
		}
		_, err = pg.CreateProjectKey(ctx, organisationID, projectID, keyID, name, sum, "")
		if errors.Is(err, store.ErrKeyExists) && attempt == 0 {
			continue
		}
		if err != nil {
			return "", err
		}
		return full, nil
	}
}

// RevokeKey revokes a project's key by its key_id. A key_id that is not this project's is
// store.ErrNotFound.
func RevokeKey(ctx context.Context, c config.Config, organisationID, projectID, keyID string) error {
	pool, err := pgxpool.New(ctx, c.PostgresURL)
	if err != nil {
		return fmt.Errorf("postgres: %w", redactErr(err, c.PostgresURL))
	}
	defer pool.Close()
	return pgstore.NewStore(pool, nil).RevokeProjectKey(ctx, organisationID, projectID, keyID)
}

// WorkerEndpointCommand is what `voicegateway project workers` acts on: a project and, for add, the
// worker's metrics URL or, for remove, the endpoint's id.
type WorkerEndpointCommand struct {
	OrganisationID string
	ProjectID      string
}

// workerEndpoints opens the store a workers command acts through.
func workerEndpoints(ctx context.Context, c config.Config, in WorkerEndpointCommand) (*pgstore.Store, func(), error) {
	if strings.TrimSpace(in.ProjectID) == "" {
		return nil, nil, errors.New("missing --project")
	}
	sealer, err := seal.New(c.SecretKey)
	if err != nil {
		return nil, nil, err
	}
	pool, err := pgxpool.New(ctx, c.PostgresURL)
	if err != nil {
		return nil, nil, fmt.Errorf("postgres: %w", redactErr(err, c.PostgresURL))
	}
	return pgstore.NewStore(pool, sealer), pool.Close, nil
}

// AddWorkerEndpoint lists a self-hosted worker's Prometheus metrics URL for a project. The URL is
// sealed under the configured key; only its scheme and host are stored in the clear.
func AddWorkerEndpoint(ctx context.Context, c config.Config, in WorkerEndpointCommand, rawURL string) (store.WorkerEndpoint, error) {
	if strings.TrimSpace(rawURL) == "" {
		return store.WorkerEndpoint{}, errors.New("missing the worker metrics URL")
	}
	s, done, err := workerEndpoints(ctx, c, in)
	if err != nil {
		return store.WorkerEndpoint{}, err
	}
	defer done()
	return s.AddWorkerEndpoint(ctx, orgOrDefault(in.OrganisationID), in.ProjectID, rawURL)
}

// RemoveWorkerEndpoint drops one of a project's listed endpoints by id.
func RemoveWorkerEndpoint(ctx context.Context, c config.Config, in WorkerEndpointCommand, id string) error {
	s, done, err := workerEndpoints(ctx, c, in)
	if err != nil {
		return err
	}
	defer done()
	return s.RemoveWorkerEndpoint(ctx, orgOrDefault(in.OrganisationID), in.ProjectID, strings.TrimSpace(id))
}

// ListWorkerEndpoints lists a project's endpoints by id and host, never their URLs.
func ListWorkerEndpoints(ctx context.Context, c config.Config, in WorkerEndpointCommand) ([]store.WorkerEndpoint, error) {
	s, done, err := workerEndpoints(ctx, c, in)
	if err != nil {
		return nil, err
	}
	defer done()
	org := orgOrDefault(in.OrganisationID)
	if _, err := s.Project(ctx, org, in.ProjectID); err != nil {
		return nil, err
	}
	return s.WorkerEndpoints(ctx, org, in.ProjectID)
}

func orgOrDefault(org string) string {
	if org == "" {
		return "default"
	}
	return org
}
