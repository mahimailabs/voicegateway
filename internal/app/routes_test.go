package app

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// TestPostOnlyRoutes: a POST-only path answers 405 to any other method, even with the console's
// catch-all on "/", and POST still reaches its handler.
func TestPostOnlyRoutes(t *testing.T) {
	mux := http.NewServeMux()
	handlePost(mux, map[string]http.Handler{
		"/livekit/webhook": http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusAccepted) }),
	})
	mux.HandleFunc("/", func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte("console")) })

	tests := []struct {
		method string
		status int
		allow  string
	}{
		{http.MethodPost, http.StatusAccepted, ""},
		{http.MethodGet, http.StatusMethodNotAllowed, http.MethodPost},
		{http.MethodHead, http.StatusMethodNotAllowed, http.MethodPost},
		{http.MethodPut, http.StatusMethodNotAllowed, http.MethodPost},
	}
	for _, tt := range tests {
		t.Run(tt.method, func(t *testing.T) {
			rec := httptest.NewRecorder()
			mux.ServeHTTP(rec, httptest.NewRequest(tt.method, "/livekit/webhook", nil))
			if rec.Code != tt.status {
				t.Fatalf("status %d, want %d", rec.Code, tt.status)
			}
			if got := rec.Header().Get("Allow"); got != tt.allow {
				t.Errorf("Allow %q, want %q", got, tt.allow)
			}
		})
	}
}
