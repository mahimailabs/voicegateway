package app

import (
	"context"
	"log/slog"
	"net/http"
	"time"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/ingest"
	"github.com/mahimailabs/voicegateway/internal/store"
)

type ingestionProjects interface {
	Project(context.Context, string, string) (store.Project, error)
	LiveKitSecret(context.Context, string, string) (string, error)
}

// mountIngestion resolves the destination from the URL before LiveKit verifies
// credentials. Identical key strings on independent servers never choose a tenant.
func mountIngestion(mux *http.ServeMux, projects ingestionProjects, stream ingest.Appender, quota ingest.Quota, audio ingest.Audio, log *slog.Logger, now func() time.Time) {
	factories := map[string]func(ingest.Projects) http.Handler{
		"/livekit/webhook": func(p ingest.Projects) http.Handler {
			return &ingest.Webhooks{Quota: quota, Projects: p, Stream: stream, Log: log, Now: now}
		},
		"/observability/recordings/v0": func(p ingest.Projects) http.Handler {
			u := &ingest.Uploads{Quota: quota, Projects: p, Stream: stream, Audio: audio, Log: log, Now: now}
			return http.HandlerFunc(u.SessionReport)
		},
	}
	for path, kind := range map[string]event.Kind{
		"/observability/traces/otlp/v0":  event.KindAgentTraces,
		"/observability/logs/otlp/v0":    event.KindAgentLogs,
		"/observability/metrics/otlp/v0": event.KindAgentMetrics,
	} {
		factories[path] = func(p ingest.Projects) http.Handler {
			u := &ingest.Uploads{Quota: quota, Projects: p, Stream: stream, Log: log, Now: now}
			return u.OTLP(kind)
		}
	}
	for path, factory := range factories {
		handlePost(mux, map[string]http.Handler{
			"/ingest/{organisationID}/{projectID}" + path: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				factory(ingest.ScopedProjects{
					Store: projects, OrganisationID: r.PathValue("organisationID"), ProjectID: r.PathValue("projectID"),
				}).ServeHTTP(w, r)
			}),
		})
		// Never let an obsolete upload URL fall through to the console HTML.
		mux.HandleFunc(path, http.NotFound)
	}
}
