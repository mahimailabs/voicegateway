package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/livekit/protocol/livekit"

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
		a.Gate = BearerToken(configured)
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
	a := &API{Gate: BearerToken("0123456789abcdef0123456789abcdef")}
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
	a := &API{Gate: BearerToken(token)}
	for _, route := range []string{"/media-server", "/sip"} {
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
