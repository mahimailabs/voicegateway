package api

import (
	"net/http"
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
		{"right token", token, "Bearer " + token, http.StatusOK},
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
