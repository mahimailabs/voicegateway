package app

import (
	"bytes"
	"context"
	"crypto/tls"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/alexedwards/scs/v2/memstore"

	"github.com/mahimailabs/voicegateway/internal/api"
	"github.com/mahimailabs/voicegateway/internal/auth"
	"github.com/mahimailabs/voicegateway/internal/config"
	"github.com/mahimailabs/voicegateway/internal/store/pgstore"
)

// Acceptance check 6 of phase 06c: the interim token is retired. A left-over variable warns once
// and starts; the token reads nothing; the configuration no longer carries it.
func TestInterimTokenRetired(t *testing.T) {
	var logs bytes.Buffer
	log := slog.New(slog.NewTextHandler(&logs, nil))
	env := map[string]string{"VOICEGATEWAY_API_TOKEN": "0123456789abcdef0123456789abcdef"}
	warnRetiredToken(func(k string) string { return env[k] }, log)
	if n := strings.Count(logs.String(), "VOICEGATEWAY_API_TOKEN"); n != 1 || !strings.Contains(logs.String(), "level=WARN") {
		t.Errorf("left-over token logged %q; want exactly one warning naming the variable", logs.String())
	}
	logs.Reset()
	warnRetiredToken(func(string) string { return "" }, log)
	if logs.Len() != 0 {
		t.Errorf("no token logged %q, want nothing", logs.String())
	}
	if _, ok := reflect.TypeFor[config.Config]().FieldByName("APIToken"); ok {
		t.Error("config.Config still has APIToken")
	}
	secure, plain := auth.NewSessions(memstore.New())
	gate := (&auth.Service{Secure: secure, Plain: plain, Log: log, Now: time.Now}).Gate
	routes := (&api.API{Log: log, Now: time.Now, Gate: gate}).Routes()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/org/default/project/p/calls", nil)
	req.Header.Set("Authorization", "Bearer "+env["VOICEGATEWAY_API_TOKEN"])
	rec := httptest.NewRecorder()
	routes.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Errorf("a request bearing the old token = %d, want 401", rec.Code)
	}
}

// Acceptance check 7 of phase 06c: key create prints one key that reads its project, and key
// revoke --key-id makes it 401.
func TestKeyCommands(t *testing.T) {
	ctx := context.Background()
	pool, url := newPostgres(t)
	cfg := config.Config{PostgresURL: url}
	if _, err := pool.Exec(ctx, `INSERT INTO organisations (id, name) VALUES ('org-a', 'A')`); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `INSERT INTO projects (organisation_id, id, name, livekit_url, livekit_api_key, livekit_api_secret_sealed) VALUES ('org-a', 'p', 'P', 'wss://x', 'APIp', '\x00')`); err != nil {
		t.Fatal(err)
	}
	if _, err := CreateKey(ctx, cfg, "org-a", "nothing", "ci"); err == nil {
		t.Error("a key for an unknown project was created")
	}
	for _, name := range []string{"   ", "a\x00b", strings.Repeat("x", 65)} {
		if _, err := CreateKey(ctx, cfg, "org-a", "p", name); err == nil || err.Error() != "--name must be 1 to 64 characters, with no control characters" {
			t.Errorf("key create --name %q: err %v, want the name rule", name, err)
		}
	}
	full, err := CreateKey(ctx, cfg, "org-a", "p", "ci")
	if err != nil {
		t.Fatal(err)
	}
	pg := pgstore.NewStore(pool, nil)
	secure, plain := auth.NewSessions(memstore.New())
	quiet := slog.New(slog.NewTextHandler(io.Discard, nil))
	gate := (&auth.Service{Users: pg, Secure: secure, Plain: plain, Log: quiet, Now: time.Now}).Gate
	routes := (&api.API{Projects: pg, Settings: pg, Metadata: pg, Calls: pg, Log: quiet, Now: time.Now, Gate: gate}).Routes()
	read := func() int {
		req := httptest.NewRequest(http.MethodGet, "/api/v1/org/org-a/project/p/calls", nil)
		req.TLS = &tls.ConnectionState{}
		req.Header.Set("Authorization", "Bearer "+full)
		rec := httptest.NewRecorder()
		routes.ServeHTTP(rec, req)
		return rec.Code
	}
	if code := read(); code != http.StatusOK {
		t.Fatalf("the printed key reads = %d, want 200", code)
	}
	if err := RevokeKey(ctx, cfg, "org-a", "p", full[4:16]); err != nil {
		t.Fatal(err)
	}
	if code := read(); code != http.StatusUnauthorized {
		t.Errorf("after key revoke = %d, want 401", code)
	}
}
