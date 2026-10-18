package api

import (
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
)

// Acceptance check 7: the interim bearer gate. No token configured is 401 for every request; a
// missing or wrong token is 401; the right one reads the call. Every 401 is the contract's Error.
func TestAPIRequiresToken(t *testing.T) {
	f := newFixture(t)
	base := time.Date(2026, 10, 17, 18, 0, 0, 0, time.UTC)
	f.append(event.KindLiveKitWebhook, "EV_1", webhook(t, &livekit.WebhookEvent{
		Event: "room_started", Id: "EV_1", CreatedAt: base.Unix(),
		Room: &livekit.Room{Sid: "RM_GATE", Name: "gated", CreationTime: base.Unix()},
	}))
	f.store()
	p := path(f.project, f.callID("RM_GATE"))
	const token = "0123456789abcdef0123456789abcdef"

	gated := func(configured string) *API {
		a := f.api(nil)
		a.Gate = TokenGate(configured)
		return a
	}
	for _, tt := range []struct {
		name, configured, auth string
		want                   int
	}{
		{"no token configured", "", "Bearer " + token, http.StatusUnauthorized},
		{"no header", token, "", http.StatusUnauthorized},
		{"wrong token", token, "Bearer 0123456789abcdef0123456789abcdee", http.StatusUnauthorized},
		{"not a bearer", token, token, http.StatusUnauthorized},
		{"empty bearer", token, "Bearer ", http.StatusUnauthorized},
		{"right token", token, "Bearer " + token, http.StatusOK},
		{"scheme in lower case", token, "bearer " + token, http.StatusOK},
	} {
		t.Run(tt.name, func(t *testing.T) {
			var body map[string]any
			if code := f.getAuth(gated(tt.configured), p, tt.auth, &body); code != tt.want {
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
	a := &API{Gate: TokenGate("0123456789abcdef0123456789abcdef")}
	for _, tt := range []struct {
		auth string
		want int
	}{
		{"", http.StatusUnauthorized},
		{"Bearer 0123456789abcdef0123456789abcdef", http.StatusNotFound},
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

// The LiveKit layer reads sit behind the same gate: a missing or wrong token is 401 in the
// contract's Error shape before any store is read.
func TestLayerRoutesAreGated(t *testing.T) {
	const token = "0123456789abcdef0123456789abcdef"
	a := &API{Gate: TokenGate(token)}
	for _, route := range []string{"/media-server", "/sip", "/egress", "/ingress"} {
		for _, auth := range []string{"", "Bearer 0123456789abcdef0123456789abcdee", token} {
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

// TokenGate is the production gate, auth.Service.Gate, over an in-memory session store with only
// the interim token configured: for tests of the API behind it. Exported for the package's external
// tests.
func TokenGate(token string) func(http.Handler) http.Handler {
	secure, plain := auth.NewSessions(memstore.New())
	return (&auth.Service{Secure: secure, Plain: plain, Token: token, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: time.Now}).Gate
}
