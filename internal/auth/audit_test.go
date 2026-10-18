package auth_test

import (
	"context"
	"encoding/base64"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/alexedwards/scs/pgxstore"
	"github.com/alexedwards/scs/v2"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"golang.org/x/crypto/argon2"

	"github.com/mahimailabs/voicegateway/internal/auth"
	"github.com/mahimailabs/voicegateway/internal/store/pgstore"
)

// queryCounter counts every query a pool sends: its round trips to Postgres.
type queryCounter struct{ n atomic.Int64 }

func (q *queryCounter) TraceQueryStart(ctx context.Context, _ *pgx.Conn, _ pgx.TraceQueryStartData) context.Context {
	q.n.Add(1)
	return ctx
}
func (*queryCounter) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

// tracedService is the sign-in service over a pool whose queries are counted, with sessions in
// store.
func (f *fixture) tracedService(wrap func(scs.CtxStore) scs.CtxStore) (*auth.Service, *queryCounter) {
	f.t.Helper()
	cfg, err := pgxpool.ParseConfig(f.url)
	if err != nil {
		f.t.Fatal(err)
	}
	counter := &queryCounter{}
	cfg.ConnConfig.Tracer = counter
	pool, err := pgxpool.NewWithConfig(context.Background(), cfg)
	if err != nil {
		f.t.Fatal(err)
	}
	f.t.Cleanup(pool.Close)
	sessions := pgxstore.NewWithCleanupInterval(pool, 0)
	secure, plain := auth.NewSessions(wrap(sessions))
	return &auth.Service{
		Users: pgstore.NewStore(pool, nil), Secure: secure, Plain: plain,
		Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: time.Now,
	}, counter
}

// #388 items 1 and 2: an authenticated API request costs 2 round trips before its handler (the
// session and the principal, one query each), and no session write while nothing changed. Before:
// 4 (the session, the user, the memberships, the session write).
func TestAuthenticatedRequestRoundTrips(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "default", "admin")
	for _, tt := range []struct {
		name string
		wrap func(scs.CtxStore) scs.CtxStore
		want int64 // round trips per request, after the first
	}{
		{"quiet store", func(s scs.CtxStore) scs.CtxStore { return auth.NewQuietStore(s, 5*time.Minute) }, 2},
		{"plain store", func(s scs.CtxStore) scs.CtxStore { return s }, 3}, // scs writes the session every time
	} {
		t.Run(tt.name, func(t *testing.T) {
			s, counter := f.tracedService(tt.wrap)
			cookie := f.signedIn(s, "ada@example.com")
			gated := s.Gate(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) }))
			f.do(gated, http.MethodGet, "/api/v1/workspaces", "", request{tls: true, cookies: []*http.Cookie{cookie}}) // warms the pool
			counter.n.Store(0)
			const requests = 10
			for range requests {
				f.do(gated, http.MethodGet, "/api/v1/workspaces", "", request{tls: true, cookies: []*http.Cookie{cookie}})
			}
			if got := counter.n.Load(); got != tt.want*requests {
				t.Errorf("%d round trips over %d requests, want %d each", got, requests, tt.want)
			}
		})
	}
}

// #388 item 4: a hash stored with other parameters is made again at the next sign-in, with the
// password the sign-in proved, and the password version stays, so no session is signed out.
func TestSignInRehashesOldParameters(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "default", "admin")
	salt := []byte("0123456789abcdef")
	key := argon2.IDKey([]byte(password), salt, 2, 19*1024, 1, 32)
	b64 := base64.RawStdEncoding
	old := fmt.Sprintf("$argon2id$v=%d$m=%d,t=2,p=1$%s$%s", argon2.Version, 19*1024, b64.EncodeToString(salt), b64.EncodeToString(key))
	ctx := context.Background()
	if _, err := f.pool.Exec(ctx, `UPDATE users SET password_hash = $1 WHERE email = 'ada@example.com'`, old); err != nil {
		t.Fatal(err)
	}
	f.signedIn(f.service(), "ada@example.com")
	var stored string
	var version int
	if err := f.pool.QueryRow(ctx, `SELECT password_hash, password_version FROM users WHERE email = 'ada@example.com'`).Scan(&stored, &version); err != nil {
		t.Fatal(err)
	}
	if stored == old || auth.NeedsRehash(stored) || !strings.HasPrefix(stored, "$argon2id$v=19$m=65536,t=3,p=4$") {
		t.Errorf("stored hash after sign-in = %q, want the current parameters", stored)
	}
	if version != 0 {
		t.Errorf("password_version = %d, want 0: a rehash is not a new password", version)
	}
	f.signedIn(f.service(), "ada@example.com") // the rehashed password still signs in
}
