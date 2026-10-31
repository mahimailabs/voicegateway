package scrape

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// A failed read logs where it failed (Twirp code, HTTP status, client step, cause type) beside its
// class, so an "error" class can be told apart in the log, and never LiveKit's own message.
func TestFailureSaysWhereWithoutLiveKitsMessage(t *testing.T) {
	const message = "livekit-said-this"
	answer := func(status int, body string) http.HandlerFunc {
		return func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(status)
			_, _ = w.Write([]byte(body))
		}
	}
	for _, c := range []struct {
		name    string
		handler http.HandlerFunc
		tls     bool
		class   string
		want    map[string]string
	}{
		{
			"reply that is not a Twirp error", answer(http.StatusBadRequest, message), false, store.ErrorOther,
			map[string]string{"twirp_code": "internal", "http_status": "400", "cause": "*twirp.twerr"},
		},
		{
			"Twirp error from LiveKit", answer(http.StatusNotFound, `{"code":"not_found","msg":"`+message+`"}`), false, store.ErrorOther,
			map[string]string{"twirp_code": "not_found", "cause": "*twirp.twerr"},
		},
		{
			"reply that is not protobuf", answer(http.StatusOK, message), false, store.ErrorOther,
			map[string]string{"twirp_code": "internal", "client_step": "failed to unmarshal proto response"},
		},
		{
			"untrusted certificate", answer(http.StatusOK, ""), true, store.ErrorUnreachable,
			map[string]string{"twirp_code": "internal", "client_step": "failed to do request", "cause": "x509.UnknownAuthorityError"},
		},
	} {
		t.Run(c.name, func(t *testing.T) {
			srv := httptest.NewUnstartedServer(c.handler)
			if c.tls {
				srv.StartTLS()
			} else {
				srv.Start()
			}
			defer srv.Close()
			t.Setenv("LK_AGENTS_URL", srv.URL)
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			_, err := DialSDK(srv.URL, "APIkey", "a-test-secret-not-real").ListAgents(ctx)
			if err == nil {
				t.Fatal("ListAgents succeeded, want a failure")
			}
			if got := classify(err); got != c.class {
				t.Errorf("class %q, want %q", got, c.class)
			}
			var buf bytes.Buffer
			slog.New(slog.NewJSONHandler(&buf, nil)).Warn("read failed", failure(err))
			if strings.Contains(buf.String(), message) {
				t.Errorf("log %s holds LiveKit's message", buf.String())
			}
			var line struct{ Failure map[string]string }
			if err := json.Unmarshal(buf.Bytes(), &line); err != nil {
				t.Fatal(err)
			}
			for k, v := range c.want {
				if !strings.Contains(line.Failure[k], v) {
					t.Errorf("failure.%s = %q, want %q (log %s)", k, line.Failure[k], v, buf.String())
				}
			}
		})
	}
}
