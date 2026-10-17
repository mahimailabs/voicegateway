package app

import (
	"context"
	"errors"
	"os"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/testcontainers/testcontainers-go"
	tcpostgres "github.com/testcontainers/testcontainers-go/modules/postgres"

	"github.com/mahimailabs/voicegateway/internal/config"
	"github.com/mahimailabs/voicegateway/internal/store/pgstore"
)

// Acceptance check 1 of phase 06a.
func TestUserAddCreatesFirstAdmin(t *testing.T) {
	if os.Getenv("CI") == "" {
		if testing.Short() {
			t.Skip("needs Docker; runs in CI")
		}
		testcontainers.SkipIfProviderIsNotHealthy(t)
	}
	ctx := context.Background()
	c, err := tcpostgres.Run(ctx, postgresImage,
		tcpostgres.WithDatabase("voicegateway"), tcpostgres.WithUsername("voicegateway"), tcpostgres.WithPassword("pg-test"),
		tcpostgres.BasicWaitStrategies())
	testcontainers.CleanupContainer(t, c)
	if err != nil {
		t.Fatal(err)
	}
	url, err := c.ConnectionString(ctx, "sslmode=disable")
	if err != nil {
		t.Fatal(err)
	}
	pool, err := pgxpool.New(ctx, url)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	if _, err := pgstore.Migrate(ctx, pool); err != nil {
		t.Fatal(err)
	}
	cfg := config.Config{PostgresURL: url}
	const password = "a long enough password"

	if _, err := AddUser(ctx, cfg, "Admin@Example.com", "default", password); err != nil {
		t.Fatal(err)
	}
	var users, admins int
	var hash string
	if err := pool.QueryRow(ctx, `SELECT count(*), max(password_hash) FROM users`).Scan(&users, &hash); err != nil {
		t.Fatal(err)
	}
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM memberships WHERE role = 'admin' AND organisation_id = 'default'`).Scan(&admins); err != nil {
		t.Fatal(err)
	}
	if users != 1 || admins != 1 || !strings.HasPrefix(hash, "$argon2id$") || strings.Contains(hash, password) {
		t.Fatalf("users %d, admins %d, hash %q; want 1, 1 and an argon2id hash without the password", users, admins, hash)
	}
	if _, err := AddUser(ctx, cfg, "admin@example.com", "default", password); !errors.Is(err, pgstore.ErrUserExists) {
		t.Errorf("second add err = %v, want ErrUserExists", err)
	}
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM users`).Scan(&users); err != nil || users != 1 {
		t.Errorf("users after a refused add = %d, %v; want 1", users, err)
	}
}
