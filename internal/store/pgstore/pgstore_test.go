package pgstore

import (
	"bytes"
	"context"
	"errors"
	"os"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/testcontainers/testcontainers-go"
	tcpostgres "github.com/testcontainers/testcontainers-go/modules/postgres"

	"github.com/mahimailabs/voicegateway/internal/seal"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// The same image, tag and digest as deploy/compose.yaml.
const postgresImage = "postgres:16.15-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"

// startPostgres runs an empty Postgres. It skips with -short or without Docker on a laptop; in CI a
// missing Docker fails the test (CLAUDE.md Code quality).
func startPostgres(t *testing.T) *pgxpool.Pool {
	t.Helper()
	if os.Getenv("CI") == "" {
		if testing.Short() {
			t.Skip("needs Docker; runs in CI")
		}
		testcontainers.SkipIfProviderIsNotHealthy(t)
	}
	ctx := context.Background()
	pg, err := tcpostgres.Run(ctx, postgresImage,
		tcpostgres.WithDatabase("voicegateway"), tcpostgres.WithUsername("voicegateway"), tcpostgres.WithPassword("pg-test"),
		tcpostgres.BasicWaitStrategies())
	testcontainers.CleanupContainer(t, pg)
	if err != nil {
		t.Fatalf("start postgres: %v", err)
	}
	url, err := pg.ConnectionString(ctx, "sslmode=disable")
	if err != nil {
		t.Fatal(err)
	}
	pool, err := pgxpool.New(ctx, url)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	return pool
}

func migrated(t *testing.T) (*Store, *pgxpool.Pool) {
	t.Helper()
	pool := startPostgres(t)
	if _, err := Migrate(context.Background(), pool); err != nil {
		t.Fatal(err)
	}
	sealer, err := seal.New(bytes.Repeat([]byte{7}, seal.KeySize))
	if err != nil {
		t.Fatal(err)
	}
	return NewStore(pool, sealer), pool
}

func project(id, key string) NewProject {
	return NewProject{
		OrganisationID: "default", ID: id, Name: "Project " + id,
		LiveKitURL: "wss://example.livekit.cloud", LiveKitAPIKey: key, LiveKitAPISecret: "lk-secret-" + id,
	}
}

func TestMigrateTwice(t *testing.T) {
	pool := startPostgres(t)
	ctx := context.Background()
	first, err := Migrate(ctx, pool)
	if err != nil {
		t.Fatalf("first migrate: %v", err)
	}
	if first == 0 {
		t.Fatal("first migrate applied nothing")
	}
	second, err := Migrate(ctx, pool)
	if err != nil {
		t.Fatalf("second migrate: %v", err)
	}
	if second != 0 {
		t.Errorf("second migrate applied %d migrations, want 0", second)
	}
	var org string
	if err := pool.QueryRow(ctx, "SELECT name FROM organisations WHERE id = 'default'").Scan(&org); err != nil {
		t.Errorf("default organisation missing after migrate: %v", err)
	}
	for _, table := range []string{"calls", "call_participants"} {
		if _, err := pool.Exec(ctx, "SELECT count(*) FROM "+table); err != nil {
			t.Errorf("table %s after migrate: %v", table, err)
		}
	}
}

func TestProjectSecretSealed(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	p, err := s.CreateProject(ctx, project("alpha", "APIalpha"))
	if err != nil {
		t.Fatal(err)
	}
	if p.OrganisationID != "default" || p.ID != "alpha" || p.LiveKitAPIKey != "APIalpha" || p.CreatedAt.IsZero() {
		t.Errorf("CreateProject() = %+v", p)
	}

	var stored []byte
	if err := pool.QueryRow(ctx, "SELECT livekit_api_secret_sealed FROM projects WHERE id = 'alpha'").Scan(&stored); err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(stored, []byte("lk-secret-alpha")) {
		t.Fatal("the stored value contains the plaintext secret")
	}
	secret, err := s.LiveKitSecret(ctx, "default", "alpha")
	if err != nil || secret != "lk-secret-alpha" {
		t.Errorf("LiveKitSecret() = %q, %v; want the original secret", secret, err)
	}

	found, err := s.ProjectByLiveKitKey(ctx, "APIalpha")
	if err != nil || found.ID != "alpha" {
		t.Errorf("ProjectByLiveKitKey() = %+v, %v", found, err)
	}
	if _, err := s.ProjectByLiveKitKey(ctx, "APIunknown"); !errors.Is(err, store.ErrNotFound) {
		t.Errorf("unknown key err = %v, want ErrNotFound", err)
	}
}

func TestProjectDuplicateLiveKitKey(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	if _, err := s.CreateProject(ctx, project("alpha", "APIshared")); err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		name    string
		p       NewProject
		wantErr error
		names   string
	}{
		{name: "key used by another project", p: project("beta", "APIshared"), wantErr: store.ErrLiveKitKeyInUse, names: "default/alpha"},
		{name: "same id again", p: project("alpha", "APIother"), wantErr: store.ErrProjectExists},
		{name: "unknown organisation", p: NewProject{OrganisationID: "nope", ID: "x", Name: "X", LiveKitURL: "wss://x", LiveKitAPIKey: "APIx", LiveKitAPISecret: "s"}, wantErr: store.ErrNotFound},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := s.CreateProject(ctx, tt.p)
			if !errors.Is(err, tt.wantErr) {
				t.Fatalf("CreateProject() err = %v, want %v", err, tt.wantErr)
			}
			if tt.names != "" && !strings.Contains(err.Error(), tt.names) {
				t.Errorf("error %q does not name project %s", err, tt.names)
			}
		})
	}

	var n int
	if err := pool.QueryRow(ctx, "SELECT count(*) FROM projects").Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Errorf("projects = %d after refused creates, want 1", n)
	}
}
