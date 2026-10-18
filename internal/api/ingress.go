package api

import (
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

type ingressRead struct {
	Source       string        `json:"source"`
	Ingress      []ingressItem `json:"ingress"`
	SourceStatus sourceStatus  `json:"sourceStatus"`
}

type ingressItem struct {
	store.Ingress
	// CreatedAt is always null: IngressInfo has no creation time, and the worker does not see an
	// endpoint being created.
	CreatedAt *time.Time       `json:"createdAt"`
	History   []store.StatusAt `json:"history"`
}

const ingressSource = "Sampled by the worker every 15 s with the project's API key: IngressService ListIngress, " +
	"which shows each endpoint's current or last session only. Status histories join the samples with the " +
	"stored ingress_started and ingress_ended webhooks. Stream keys and the credentials and query values " +
	"of endpoint URLs are dropped before storage. LiveKit reports no creation time for an endpoint, so " +
	"createdAt is null."

func (a *API) ingress(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	org, proj := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
	st, ok := a.sampledLayer(w, r, store.LayerIngress, "the project's API key has no ingressAdmin grant, which ListIngress needs")
	if !ok {
		return
	}
	seen, err := a.Layers.IngressSince(ctx, org, proj, a.Now().Add(-ioSince), ioItems)
	if err != nil {
		a.internal(w, r, "ingress", err)
		return
	}
	ids := make([]string, len(seen))
	for i, in := range seen {
		ids[i] = in.Ingress.ID
	}
	changes, err := a.Layers.IngressChanges(ctx, org, proj, ids)
	if err != nil {
		a.internal(w, r, "ingress changes", err)
		return
	}
	hooks, err := a.Layers.IngressWebhooks(ctx, org, proj, ids, a.Now().Add(-hookSince))
	if err != nil {
		a.internal(w, r, "ingress webhooks", err)
		return
	}
	byID := histories(changes, hooks)
	items := make([]ingressItem, 0, len(seen))
	for _, in := range seen {
		item := ingressItem{Ingress: in.Ingress, History: byID[in.Ingress.ID].history}
		if item.Published == nil {
			item.Published = []store.IngressTrack{}
		}
		if item.History == nil {
			item.History = []store.StatusAt{}
		}
		items = append(items, item)
	}
	writeJSON(w, http.StatusOK, ingressRead{Source: ingressSource, Ingress: items, SourceStatus: statusOf(st)})
}
