package api

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/alexedwards/scs/v2/memstore"
	"github.com/livekit/protocol/livekit"

	"github.com/mahimailabs/voicegateway/internal/auth"
	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// The gate with project keys (phase 06c, which retired the interim token): a missing, wrong or
// malformed key is 401 in the contract's Error shape, and a key reads its own project's call.
func TestAPIRequiresKey(t *testing.T) {
	f := newFixture(t)
	base := time.Date(2026, 10, 17, 18, 0, 0, 0, time.UTC)
	f.append(event.KindLiveKitWebhook, "EV_1", webhook(t, &livekit.WebhookEvent{
		Event: "room_started", Id: "EV_1", CreatedAt: base.Unix(),
		Room: &livekit.Room{Sid: "RM_GATE", Name: "gated", CreationTime: base.Unix()},
	}))
	f.store()
	p := path(f.project, f.callID("RM_GATE"))
	gate, key := KeyFor("default", f.project)
	a := f.api(nil)
	a.Gate = gate
	wrong := key[:len(key)-1] + map[bool]string{true: "B", false: "A"}[key[len(key)-1] == 'A']
	for _, tt := range []struct {
		name, auth string
		want       int
	}{
		{"no header", "", http.StatusUnauthorized},
		{"wrong secret", "Bearer " + wrong, http.StatusUnauthorized},
		{"retired interim token", "Bearer 0123456789abcdef0123456789abcdef", http.StatusUnauthorized},
		{"not a bearer", key, http.StatusUnauthorized},
		{"empty bearer", "Bearer ", http.StatusUnauthorized},
		{"right key", "Bearer " + key, http.StatusOK},
		{"scheme in lower case", "bearer " + key, http.StatusOK},
	} {
		t.Run(tt.name, func(t *testing.T) {
			var body map[string]any
			if code := f.getAuth(a, p, tt.auth, &body); code != tt.want {
				t.Fatalf("status = %d, want %d (%v)", code, tt.want, body)
			}
			if tt.want == http.StatusUnauthorized && body["code"] != codeUnauthenticated {
				t.Errorf("code = %v, want unauthenticated", body["code"])
			}
		})
	}
}

// Paths outside the call routes are gated as well, and answer in the contract's Error shape.
func TestUnknownAPIPathIsGated(t *testing.T) {
	gate, key := KeyFor("default", "p")
	a := &API{Gate: gate}
	for _, tt := range []struct {
		auth string
		want int
	}{
		{"", http.StatusUnauthorized},
		{"Bearer " + key, http.StatusNotFound},
	} {
		req := httptest.NewRequest(http.MethodGet, "/api/v1/anything", nil)
		if tt.auth != "" {
			req.Header.Set("Authorization", tt.auth)
		}
		rec := httptest.NewRecorder()
		a.Routes().ServeHTTP(rec, req)
		var e apiError
		if err := json.Unmarshal(rec.Body.Bytes(), &e); err != nil || rec.Code != tt.want || e.RequestID == "" {
			t.Errorf("auth %q: status %d, body %s; want %d in the Error shape", tt.auth, rec.Code, rec.Body.String(), tt.want)
		}
	}
}

// The LiveKit layer reads sit behind the same gate: a missing, wrong or retired credential is 401
// in the contract's Error shape before any store is read.
func TestLayerRoutesAreGated(t *testing.T) {
	gate, key := KeyFor("org_1", "prj_1")
	a := &API{Gate: gate}
	for _, route := range []string{"/media-server", "/sip", "/egress", "/ingress"} {
		for _, auth := range []string{"", "Bearer 0123456789abcdef0123456789abcdee", key} {
			t.Run(route+" "+auth, func(t *testing.T) {
				req := httptest.NewRequest(http.MethodGet, "/api/v1/org/org_1/project/prj_1"+route, nil)
				if auth != "" {
					req.Header.Set("Authorization", auth)
				}
				rec := httptest.NewRecorder()
				a.Routes().ServeHTTP(rec, req)
				var e apiError
				if err := json.Unmarshal(rec.Body.Bytes(), &e); err != nil || rec.Code != http.StatusUnauthorized ||
					e.Code != codeUnauthenticated || e.RequestID == "" {
					t.Errorf("status %d, body %s; want 401 unauthenticated in the Error shape", rec.Code, rec.Body.String())
				}
			})
		}
	}
}

// A router wired without a gate denies every request, with the Bearer challenge, rather than serve
// openly; and a client's X-Request-Id never becomes the id in the body.
func TestNoGateDeniesAndRequestIDIsServers(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/v1/org/default/project/p/calls/x", nil)
	req.Header.Set("X-Request-Id", "chosen-by-the-client")
	rec := httptest.NewRecorder()
	(&API{}).Routes().ServeHTTP(rec, req)
	var e apiError
	if err := json.Unmarshal(rec.Body.Bytes(), &e); err != nil {
		t.Fatal(err)
	}
	if rec.Code != http.StatusUnauthorized || e.Code != codeUnauthenticated {
		t.Errorf("no gate: status %d, body %+v; want 401 unauthenticated", rec.Code, e)
	}
	if got := rec.Header().Get("WWW-Authenticate"); !strings.HasPrefix(got, "Bearer") {
		t.Errorf("WWW-Authenticate = %q, want a Bearer challenge", got)
	}
	if e.RequestID == "" || e.RequestID == "chosen-by-the-client" {
		t.Errorf("requestId = %q, want one the server made", e.RequestID)
	}
}

// keyStore holds project keys in memory for the gate; the rest of auth.Users is not used by it.
type keyStore struct {
	auth.Users
	keys map[string]store.StoredKey
}

func (k keyStore) ProjectKey(_ context.Context, keyID string) (store.StoredKey, error) {
	v, ok := k.keys[keyID]
	if !ok {
		return store.StoredKey{}, store.ErrNotFound
	}
	return v, nil
}

func (keyStore) TouchProjectKey(context.Context, string) error { return nil }

// KeyFor is the production gate, auth.Service.Gate, over an in-memory session store, holding one
// project key for org/project, and that key: for tests of the API behind it. Exported for the
// package's external tests.
func KeyFor(org, project string) (func(http.Handler) http.Handler, string) {
	keyID, sum, full, err := auth.NewKey()
	if err != nil {
		panic(err) // a test helper: crypto/rand failing ends the test run
	}
	secure, plain := auth.NewSessions(memstore.New())
	users := keyStore{keys: map[string]store.StoredKey{keyID: {KeyID: keyID, OrganisationID: org, ProjectID: project, SecretSHA256: sum}}}
	return (&auth.Service{Users: users, Secure: secure, Plain: plain, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: time.Now}).Gate, full
}
