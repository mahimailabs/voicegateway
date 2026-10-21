package api

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Connections reads how a project's ingestion last went and its first stored call.
type Connections interface {
	Connections(ctx context.Context, organisationID, projectID string) ([]store.Connection, error)
	FirstCall(ctx context.Context, organisationID, projectID string) (store.FirstCall, error)
}

type connectionURLs struct {
	Observability *string `json:"observability"`
	Webhook       *string `json:"webhook"`
}

type connectionSource struct {
	LastAcceptedAt *time.Time `json:"lastAcceptedAt"`
	LastRejectedAt *time.Time `json:"lastRejectedAt"`
	Reason         *string    `json:"reason"`
	UsedKey        *string    `json:"usedKey"`
	ExpectedKey    *string    `json:"expectedKey"`
}

type firstCall struct {
	ID        string     `json:"id"`
	StartedAt *time.Time `json:"startedAt"`
}

type projectConnection struct {
	URLs      connectionURLs   `json:"urls"`
	Webhook   connectionSource `json:"webhook"`
	Agent     connectionSource `json:"agent"`
	SDK       connectionSource `json:"sdk"`
	FirstCall *firstCall       `json:"firstCall"`
}

// getConnection answers the console's "Connect your agent" panel: the project's scoped ingestion
// URLs, the last outcome of each source and the first call the worker stored. Sample data never
// reaches the call index, so it never counts as a first call.
func (a *API) getConnection(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if a.Connections == nil || a.Projects == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "connection state unavailable")
		return
	}
	org, id := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
	project, err := a.Projects.Project(r.Context(), org, id)
	if err != nil {
		a.failWith(w, r, err, "project not found", "the connection could not be read")
		return
	}
	conns, err := a.Connections.Connections(r.Context(), org, id)
	if err != nil {
		a.failWith(w, r, err, "", "the connection could not be read")
		return
	}
	out := projectConnection{URLs: ingestionURLs(a.PublicURL, org, id)}
	for _, c := range conns {
		src := connectionSource{LastAcceptedAt: c.LastAcceptedAt, LastRejectedAt: c.LastRejectedAt, Reason: c.Reason}
		if c.Reason != nil && *c.Reason == store.ReasonKeyMismatch {
			key := project.LiveKitAPIKey
			src.UsedKey, src.ExpectedKey = c.UsedKey, &key
		}
		switch c.Source {
		case store.SourceWebhook:
			out.Webhook = src
		case store.SourceAgent:
			out.Agent = src
		case store.SourceSDK:
			out.SDK = src
		}
	}
	first, err := a.Connections.FirstCall(r.Context(), org, id)
	switch {
	case err == nil:
		out.FirstCall = &firstCall{ID: first.ID, StartedAt: first.StartedAt}
	case !errors.Is(err, store.ErrNotFound):
		a.failWith(w, r, err, "", "the connection could not be read")
		return
	}
	writeJSON(w, http.StatusOK, out)
}

// ingestionURLs are the project's scoped URLs (decision 0032), or nulls when the instance has no
// public URL to build them from.
func ingestionURLs(public, org, project string) connectionURLs {
	if public == "" {
		return connectionURLs{}
	}
	base, err := url.JoinPath(public, "ingest", org, project)
	if err != nil {
		return connectionURLs{}
	}
	hook, err := url.JoinPath(base, "livekit", "webhook")
	if err != nil {
		return connectionURLs{}
	}
	return connectionURLs{Observability: &base, Webhook: &hook}
}
