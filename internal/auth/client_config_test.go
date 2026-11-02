package auth_test

import (
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/alexedwards/scs/v2/memstore"

	"github.com/mahimailabs/voicegateway/internal/auth"
)

// Decision 0038: the console learns whether product analytics is on before anyone signs in, and
// learns nothing else.
func TestClientConfigPostHog(t *testing.T) {
	for _, tc := range []struct {
		name   string
		client auth.ClientConfig
		want   string
	}{
		{name: "off", want: `{"sentry":null,"posthog":null}`},
		{name: "on", client: auth.ClientConfig{PostHog: &auth.PostHogClient{Key: "phc_abc", Host: "https://us.i.posthog.com"}}, want: `{"sentry":null,"posthog":{"key":"phc_abc","host":"https://us.i.posthog.com"}}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			secure, plain := auth.NewSessions(memstore.New())
			s := &auth.Service{Secure: secure, Plain: plain, Client: tc.client, Log: slog.New(slog.DiscardHandler), Now: time.Now}
			rec := httptest.NewRecorder()
			s.Routes().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/auth/client-config", nil))
			b, _ := io.ReadAll(rec.Body)
			if rec.Code != http.StatusOK || string(b) != tc.want+"\n" && string(b) != tc.want {
				t.Fatalf("client-config = %d %s, want %s", rec.Code, b, tc.want)
			}
			if c := rec.Result().Cookies(); len(c) != 0 {
				t.Fatalf("client-config set cookies %v; it reads no session", c)
			}
		})
	}
}
