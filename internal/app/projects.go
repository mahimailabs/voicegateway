package app

import (
	"context"
	"fmt"
	"log/slog"
	"regexp"
	"strings"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/mahimailabs/voicegateway/internal/config"
	"github.com/mahimailabs/voicegateway/internal/seal"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/store/pgstore"
)

// Migrate applies every pending Postgres migration.
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
	for name, v := range map[string]string{"--name": in.Name, "--livekit-url": in.LiveKitURL, "--livekit-api-key": in.LiveKitAPIKey, "LIVEKIT_API_SECRET": in.LiveKitAPISecret} {
		if strings.TrimSpace(v) == "" {
			missing = append(missing, name)
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
