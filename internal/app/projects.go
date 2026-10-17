package app

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"regexp"
	"strings"

	"github.com/jackc/pgx/v5/pgxpool"

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

var nonSlug = regexp.MustCompile(`[^a-z0-9]+`)

// Slug turns a project name into its URL id, the same way the console does: "Default project"
// becomes "default-project".
func Slug(name string) string {
	return strings.Trim(nonSlug.ReplaceAllString(strings.ToLower(name), "-"), "-")
}

// AddProject creates a project with its LiveKit secret sealed under the configured key.
func AddProject(ctx context.Context, c config.Config, in ProjectInput) (store.Project, error) {
	var missing []string
	// In a fixed order, so the message reads the same on every run.
	for _, f := range []struct{ name, value string }{
		{"--name", in.Name}, {"--livekit-url", in.LiveKitURL}, {"--livekit-api-key", in.LiveKitAPIKey}, {"LIVEKIT_API_SECRET", in.LiveKitAPISecret},
	} {
		if strings.TrimSpace(f.value) == "" {
			missing = append(missing, f.name)
		}
	}
	if len(missing) > 0 {
		return store.Project{}, fmt.Errorf("missing %s", strings.Join(missing, ", "))
	}
	if in.ID == "" {
		in.ID = Slug(in.Name)
	}
	if in.OrganisationID == "" {
		in.OrganisationID = "default"
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
	return pgstore.NewStore(pool, sealer).CreateProject(ctx, pgstore.NewProject{
		OrganisationID:   in.OrganisationID,
		ID:               in.ID,
		Name:             strings.TrimSpace(in.Name),
		LiveKitURL:       strings.TrimSpace(in.LiveKitURL),
		LiveKitAPIKey:    strings.TrimSpace(in.LiveKitAPIKey),
		LiveKitAPISecret: in.LiveKitAPISecret,
	})
}

// AddUser creates an account with the admin role in one organisation, for the first admin of an
// install (decision 0027). The password comes from the caller, never a flag.
func AddUser(ctx context.Context, c config.Config, email, organisationID, password string) (string, error) {
	email = strings.TrimSpace(email)
	if email == "" || !strings.Contains(email, "@") {
		return "", errors.New("--email must be an email address")
	}
	if len(password) < minPassword {
		return "", fmt.Errorf("the password must be at least %d characters", minPassword)
	}
	if organisationID == "" {
		organisationID = "default"
	}
	hash, err := auth.HashPassword(ctx, password)
	if err != nil {
		return "", err
	}
	pool, err := pgxpool.New(ctx, c.PostgresURL)
	if err != nil {
		return "", fmt.Errorf("postgres: %w", redactErr(err, c.PostgresURL))
	}
	defer pool.Close()
	return pgstore.NewStore(pool, nil).AddUser(ctx, email, hash, organisationID, "admin")
}

// minPassword is the shortest password accepted.
const minPassword = 12
