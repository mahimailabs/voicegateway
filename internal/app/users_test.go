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
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/store/pgstore"
)

// newPostgres starts a migrated Postgres and returns a pool on it and its URL.
func newPostgres(t *testing.T) (*pgxpool.Pool, string) {
	t.Helper()
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
	return pool, url
}

// Acceptance check 1 of phase 06a.
func TestUserAddCreatesFirstAdmin(t *testing.T) {
	ctx := context.Background()
	pool, url := newPostgres(t)
	cfg := config.Config{PostgresURL: url}
	const password = "a long enough password"

	pw := func() (string, error) { return password, nil }
	if _, err := AddUser(ctx, cfg, "Admin@Example.com", "default", "admin", pw); err != nil {
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
	if _, err := AddUser(ctx, cfg, "admin@example.com", "default", "admin", pw); !errors.Is(err, store.ErrMemberExists) {
		t.Errorf("second add err = %v, want ErrMemberExists", err)
	}
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM users`).Scan(&users); err != nil || users != 1 {
		t.Errorf("users after a refused add = %d, %v; want 1", users, err)
	}
}

// Acceptance check 7 of phase 06b: user add --role adds a Member or Viewer, gives an existing account
// a membership without a second user or a password, and refuses a repeat.
func TestUserAddWithRole(t *testing.T) {
	ctx := context.Background()
	pool, url := newPostgres(t)
	cfg := config.Config{PostgresURL: url}
	if _, err := pool.Exec(ctx, `INSERT INTO organisations (id, name) VALUES ('org-a', 'A'), ('org-b', 'B')`); err != nil {
		t.Fatal(err)
	}
	asked := 0
	pw := func() (string, error) { asked++; return "a long enough password", nil }
	count := func(q string) int {
		var n int
		if err := pool.QueryRow(ctx, q).Scan(&n); err != nil {
			t.Fatal(err)
		}
		return n
	}

	if created, err := AddUser(ctx, cfg, "vee@example.com", "org-a", "viewer", pw); err != nil || !created {
		t.Fatalf("new viewer: created %v, err %v", created, err)
	}
	if n := count(`SELECT count(*) FROM memberships WHERE organisation_id = 'org-a' AND role = 'viewer'`); n != 1 {
		t.Errorf("viewer memberships = %d, want 1", n)
	}

	if _, err := AddUser(ctx, cfg, "bea@example.com", "org-b", "member", pw); err != nil {
		t.Fatal(err)
	}
	asked = 0
	if created, err := AddUser(ctx, cfg, "Bea@example.com", "org-a", "member", pw); err != nil || created {
		t.Fatalf("existing account: created %v, err %v; want a membership only", created, err)
	}
	if asked != 0 {
		t.Errorf("an existing account was asked for a password %d times", asked)
	}
	if n := count(`SELECT count(*) FROM users WHERE email = 'bea@example.com'`); n != 1 {
		t.Errorf("accounts for bea = %d, want 1", n)
	}
	if n := count(`SELECT count(*) FROM memberships m JOIN users u ON u.id = m.user_id WHERE u.email = 'bea@example.com'`); n != 2 {
		t.Errorf("bea's memberships = %d, want 2", n)
	}

	before := count(`SELECT count(*) FROM memberships`)
	if _, err := AddUser(ctx, cfg, "bea@example.com", "org-a", "viewer", pw); !errors.Is(err, store.ErrMemberExists) {
		t.Errorf("repeat err = %v, want ErrMemberExists", err)
	}
	if n := count(`SELECT count(*) FROM memberships m JOIN users u ON u.id = m.user_id WHERE u.email = 'bea@example.com' AND m.organisation_id = 'org-a' AND m.role = 'member'`); n != 1 || count(`SELECT count(*) FROM memberships`) != before {
		t.Error("a refused repeat changed a membership")
	}
	if _, err := AddUser(ctx, cfg, "x@example.com", "org-a", "owner", pw); err == nil {
		t.Error("role owner was accepted")
	}
}
