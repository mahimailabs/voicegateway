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
// credentials. Identical key strings on independent servers never choose a tenant. Each request's
// outcome is recorded on the project its URL names (conns; nil records nothing).
func mountIngestion(mux *http.ServeMux, projects ingestionProjects, stream ingest.Appender, quota ingest.Quota, audio ingest.Audio, conns *ingest.Connections, log *slog.Logger, now func() time.Time) {
	type scope struct {
		projects ingest.Projects
		org, id  string
	}
	factories := map[string]func(scope) http.Handler{
		"/livekit/webhook": func(s scope) http.Handler {
			return &ingest.Webhooks{Quota: quota, Projects: s.projects, Stream: stream, Outcome: conns.For(s.org, s.id, store.SourceWebhook), Log: log, Now: now}
		},
		"/observability/recordings/v0": func(s scope) http.Handler {
			u := &ingest.Uploads{Quota: quota, Projects: s.projects, Stream: stream, Audio: audio, Outcome: conns.For(s.org, s.id, store.SourceAgent), Log: log, Now: now}
			return http.HandlerFunc(u.SessionReport)
		},
	}
	for path, kind := range map[string]event.Kind{
		"/observability/traces/otlp/v0":  event.KindAgentTraces,
		"/observability/logs/otlp/v0":    event.KindAgentLogs,
		"/observability/metrics/otlp/v0": event.KindAgentMetrics,
	} {
		factories[path] = func(s scope) http.Handler {
			u := &ingest.Uploads{Quota: quota, Projects: s.projects, Stream: stream, Outcome: conns.For(s.org, s.id, store.SourceAgent), Log: log, Now: now}
			return u.OTLP(kind)
		}
	}
	for path, factory := range factories {
		handlePost(mux, map[string]http.Handler{
			"/ingest/{organisationID}/{projectID}" + path: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				org, id := r.PathValue("organisationID"), r.PathValue("projectID")
				factory(scope{
					projects: ingest.ScopedProjects{Store: projects, OrganisationID: org, ProjectID: id},
					org:      org, id: id,
				}).ServeHTTP(w, r)
			}),
		})
		// Never let an obsolete upload URL fall through to the console HTML.
		mux.HandleFunc(path, http.NotFound)
	}
}
