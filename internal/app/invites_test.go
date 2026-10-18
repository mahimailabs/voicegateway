package app

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"testing/fstest"
)

// Acceptance check 14 of phase 06d, for the /invite page; internal/auth checks the endpoints. The
// page holds a token in its fragment: it sends no Referer and is never cached, while the rest of
// the console keeps its own caching.
func TestInvitationHeaders(t *testing.T) {
	mux := http.NewServeMux()
	mountConsole(mux, fstest.MapFS{"index.html": {Data: []byte("<!doctype html><title>VoiceGateway</title>")}})
	for _, tt := range []struct {
		path string
		want bool
	}{
		{"/invite", true},
		{"/calls", false},
	} {
		rec := httptest.NewRecorder()
		mux.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, tt.path, nil))
		marked := rec.Header().Get("Referrer-Policy") == "no-referrer" && rec.Header().Get("Cache-Control") == "no-store"
		if rec.Code != http.StatusOK || marked != tt.want {
			t.Errorf("%s = %d, Referrer-Policy %q, Cache-Control %q; want the link headers %v", tt.path, rec.Code, rec.Header().Get("Referrer-Policy"), rec.Header().Get("Cache-Control"), tt.want)
		}
	}
}
