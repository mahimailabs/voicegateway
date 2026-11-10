package obs

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/getsentry/sentry-go"
	sentryhttp "github.com/getsentry/sentry-go/http"
	"github.com/go-chi/chi/v5"
	slogsentry "github.com/samber/slog-sentry/v2"

	"github.com/mahimailabs/voicegateway/internal/config"
)

// flushTimeout is how long a stopping role waits for queued events to reach Sentry.
const flushTimeout = 2 * time.Second

// Monitor reports one role's errors and request timings to Sentry (decision 0039).
type Monitor struct {
	on bool
}

// StartSentry connects the role to Sentry when c has a DSN and returns log with every error-level
// record also sent as a Sentry event; without a DSN it returns log unchanged and a Monitor that
// does nothing. What is sent is limited by DataCollection: no cookies, query strings, bodies or
// user, and only the headers named in safeHeaders.
func StartSentry(c config.Sentry, role string, log *slog.Logger) (*slog.Logger, *Monitor, error) {
	if !c.On() {
		return log, &Monitor{}, nil
	}
	return startSentry(clientOptions(c, role), log)
}

// startSentry is StartSentry with the SDK's options built, so a test can set their Transport.
func startSentry(opts sentry.ClientOptions, log *slog.Logger) (*slog.Logger, *Monitor, error) {
	err := sentry.Init(opts)
	if err != nil {
		// The SDK's error can repeat the DSN, so it is not wrapped.
		return nil, nil, errors.New("VOICEGATEWAY_SENTRY_DSN: Sentry did not accept the DSN")
	}
	events := slogsentry.Option{Level: slog.LevelError}.NewSentryHandler()
	return slog.New(slog.NewMultiHandler(log.Handler(), events)), &Monitor{on: true}, nil
}

// clientOptions are the SDK's settings for one role.
func clientOptions(c config.Sentry, role string) sentry.ClientOptions {
	return sentry.ClientOptions{
		Dsn:              c.DSN,
		Environment:      c.Environment,
		AttachStacktrace: true,
		EnableTracing:    c.TracesSampleRate > 0,
		TracesSampler: func(ctx sentry.SamplingContext) float64 {
			// Health probes every few seconds would crowd out the requests worth timing.
			if ctx.Span != nil && strings.HasSuffix(ctx.Span.Name, " /healthz") {
				return 0
			}
			return c.TracesSampleRate
		},
		DataCollection: &sentry.DataCollection{
			UserInfo:    sentry.Set(false),
			Cookies:     &sentry.KeyValueCollectionBehavior{Mode: sentry.CollectionOff},
			QueryParams: &sentry.KeyValueCollectionBehavior{Mode: sentry.CollectionOff},
			HTTPBodies:  []sentry.BodyType{},
			HTTPHeaders: &sentry.HeaderCollectionConfig{
				Request:  &sentry.KeyValueCollectionBehavior{Mode: sentry.CollectionAllowList, Terms: safeHeaders},
				Response: &sentry.KeyValueCollectionBehavior{Mode: sentry.CollectionAllowList, Terms: safeHeaders},
			},
		},
		// A stopping role cancels its context; that is no error.
		IgnoreErrors: []string{context.Canceled.Error()},
		Tags:         map[string]string{"role": role},
	}
}

// safeHeaders are the only header values sent; every other header is sent by name alone.
var safeHeaders = []string{"Content-Type", "User-Agent", "Accept"}

// SentryHandler times each request and reports a panic, then panics again so net/http answers as
// it would without Sentry. Without a DSN it returns h. StartSentry must have run first.
func SentryHandler(c config.Sentry, h http.Handler) http.Handler {
	if !c.On() {
		return h
	}
	return sentryhttp.New(sentryhttp.Options{Repanic: true}).Handle(h)
}

// RouteFinder finds the route pattern a method and path match, "" for none: chi.Routes.Find.
type RouteFinder interface {
	Find(rctx *chi.Context, method, path string) string
}

// ChiRoute returns the chi route pattern a request matches in routes, "" for none.
func ChiRoute(routes RouteFinder) func(*http.Request) string {
	return func(r *http.Request) string {
		// chi routes on the escaped path when there is one.
		path := r.URL.Path
		if r.URL.RawPath != "" {
			path = r.URL.RawPath
		}
		return routes.Find(chi.NewRouteContext(), r.Method, path)
	}
}

// SentryRoutes names each request's transaction by the route route finds for it, as
// "GET /api/v1/org/{orgId}/project/{projectId}/calls", so one route's requests group as one
// transaction whatever IDs their URL holds; a path no route matches is named by mount. sentryhttp
// names the transaction from r.Pattern once the request is answered, and the outer ServeMux sets
// that to the mount ("/api/v1/"); a router inside sets it only on its own copy of the request. So
// the full pattern is put there. A pattern without a method gets the request's. Without a DSN it
// returns h.
func SentryRoutes(c config.Sentry, mount string, route func(*http.Request) string, h http.Handler) http.Handler {
	if !c.On() {
		return h
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		pattern := route(r)
		if pattern == "" {
			pattern = mount
		}
		if !strings.Contains(pattern, " ") {
			pattern = r.Method + " " + pattern
		}
		r.Pattern = pattern
		h.ServeHTTP(w, r)
	})
}

// Fail reports the error a role stopped with, unless it was told to stop.
func (m *Monitor) Fail(ctx context.Context, err error) {
	if m.on && err != nil && ctx.Err() == nil {
		sentry.CaptureException(err)
	}
}

// Recover reports a panic on the calling goroutine, sends what is queued and panics again. Call it
// deferred.
func (m *Monitor) Recover() {
	if !m.on {
		return
	}
	if r := recover(); r != nil {
		sentry.CurrentHub().Recover(r)
		sentry.Flush(flushTimeout)
		panic(r)
	}
}

// Flush sends what is queued before the role exits.
func (m *Monitor) Flush() {
	if m.on {
		sentry.Flush(flushTimeout)
	}
}
