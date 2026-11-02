package obs

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/getsentry/sentry-go"

	"github.com/mahimailabs/voicegateway/internal/config"
)

// The tests share the SDK's global hub, so none runs in parallel.

const testDSN = "https://public@o1.ingest.us.sentry.io/2"

func TestSentryOffWithoutDSN(t *testing.T) {
	var out bytes.Buffer
	base := Logger(&out, slog.LevelInfo, "web")
	log, monitor, err := StartSentry(config.Sentry{}, "web", base)
	if err != nil || log != base {
		t.Fatalf("StartSentry without a DSN changed the logger: %v", err)
	}
	h := http.HandlerFunc(Health)
	if got := SentryHandler(config.Sentry{}, h); got == nil {
		t.Fatal("SentryHandler returned nil")
	}
	monitor.Fail(context.Background(), errors.New("x"))
	monitor.Flush()
}

func start(t *testing.T, rate float64) (*slog.Logger, *Monitor, *sentry.MockTransport) {
	t.Helper()
	transport := &sentry.MockTransport{}
	opts := clientOptions(config.Sentry{DSN: testDSN, Environment: "test", TracesSampleRate: rate}, "web")
	opts.Transport = transport
	var out bytes.Buffer
	log, monitor, err := startSentry(opts, Logger(&out, slog.LevelInfo, "web"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { sentry.CurrentHub().BindClient(nil) })
	return log, monitor, transport
}

func TestErrorLogBecomesAnEvent(t *testing.T) {
	log, _, transport := start(t, 0)
	log.Info("worker started")
	log.Error("worker: sweep failed, retrying next period", "project", "p1", "err", errors.New("clickhouse: timeout"))
	sentry.Flush(0)
	events := transport.Events()
	if len(events) != 1 {
		t.Fatalf("got %d events, want only the error", len(events))
	}
	e := events[0]
	if e.Message != "worker: sweep failed, retrying next period" || len(e.Exception) == 0 || e.Exception[0].Value != "clickhouse: timeout" {
		t.Fatalf("event = %q %+v", e.Message, e.Exception)
	}
	if e.Tags["role"] != "web" || e.Environment != "test" {
		t.Fatalf("tags = %v, environment = %q", e.Tags, e.Environment)
	}
}

// A request's credentials, query string and body never leave the install: an embed token can ride
// in a query string, a session in a cookie, an API key in Authorization.
func TestPanicReportCarriesNoCredentials(t *testing.T) {
	_, _, transport := start(t, 1)
	h := SentryHandler(config.Sentry{DSN: testDSN}, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		panic("boom")
	}))
	r := httptest.NewRequest(http.MethodPost, "/api/v1/calls?embed=embed-secret", strings.NewReader(`{"password":"body-secret"}`))
	r.Header.Set("Authorization", "Bearer key-secret")
	r.Header.Set("Cookie", "session=cookie-secret")
	r.Header.Set("X-Invitation-Token", "invite-secret")
	r.Header.Set("User-Agent", "agent/1")
	func() {
		defer func() { _ = recover() }()
		h.ServeHTTP(httptest.NewRecorder(), r)
	}()
	sentry.Flush(0)
	var sawPanic bool
	for _, e := range transport.Events() {
		raw, err := e.MarshalJSON()
		if err != nil {
			t.Fatal(err)
		}
		for _, secret := range []string{"embed-secret", "body-secret", "key-secret", "cookie-secret", "invite-secret"} {
			if bytes.Contains(raw, []byte(secret)) {
				t.Errorf("%s event carries %s: %s", e.Type, secret, raw)
			}
		}
		if e.Type == "" && e.Request != nil {
			sawPanic = true
			if e.Request.Headers["User-Agent"] != "agent/1" {
				t.Errorf("headers = %v, want User-Agent kept", e.Request.Headers)
			}
		}
	}
	if !sawPanic {
		t.Fatal("the panic was not reported with its request")
	}
}

func TestHealthProbesAreNotTimed(t *testing.T) {
	_, _, transport := start(t, 1)
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", Health)
	mux.HandleFunc("GET /api/v1/calls", func(http.ResponseWriter, *http.Request) {})
	h := SentryHandler(config.Sentry{DSN: testDSN}, mux)
	for _, path := range []string{"/healthz", "/api/v1/calls"} {
		h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, path, nil))
	}
	sentry.Flush(0)
	var names []string
	for _, e := range transport.Events() {
		if e.Type == "transaction" {
			names = append(names, e.Transaction)
		}
	}
	if len(names) != 1 || names[0] != "GET /api/v1/calls" {
		t.Fatalf("transactions = %v, want only GET /api/v1/calls", names)
	}
}

func TestStoppingIsNotAFailure(t *testing.T) {
	_, monitor, transport := start(t, 0)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	monitor.Fail(ctx, errors.New("serve: closed"))
	monitor.Fail(context.Background(), nil)
	monitor.Fail(context.Background(), errors.New("postgres: refused"))
	sentry.Flush(0)
	if n := len(transport.Events()); n != 1 {
		t.Fatalf("got %d events, want the one failure", n)
	}
}
