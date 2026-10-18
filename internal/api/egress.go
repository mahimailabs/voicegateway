package api

import (
	"cmp"
	"errors"
	"net/http"
	"slices"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Watch limits of the egress and ingress reads: the items seen in the last day, newest first.
const (
	ioSince = 24 * time.Hour
	ioItems = 500
	// hookSince bounds the webhooks read for their histories to the samples' 30-day retention.
	hookSince = 30 * 24 * time.Hour
)

type egressRead struct {
	Source       string       `json:"source"`
	Egress       []egressItem `json:"egress"`
	Rejected     []rejection  `json:"rejected"`
	SourceStatus sourceStatus `json:"sourceStatus"`
}

type egressItem struct {
	store.Egress
	Outcome *string          `json:"outcome"`
	History []store.StatusAt `json:"history"`
	Sampled *bool            `json:"sampled"`
}

type rejection struct {
	At    *time.Time `json:"at"`
	Case  string     `json:"case"`
	Error string     `json:"error"`
}

const egressSource = "Sampled by the worker every 15 s with the project's API key: EgressService ListEgress, " +
	"which keeps recent egress only. Status histories join the samples with the stored egress_started, " +
	"egress_updated and egress_ended webhooks. The request is kept as its shape; upload credentials, " +
	"stream keys and webhook settings are dropped before storage, and stream outputs keep their scheme " +
	"and host. A request LiveKit rejects at start never becomes an egress, so ListEgress cannot show it " +
	"and rejected stays empty."

// Outcomes of an ended egress (#345): LIMIT_REACHED keeps the recording up to the limit, so it is cut
// short, not lost; FAILED and ABORTED lose it.
const (
	outcomeRecorded = "recorded"
	outcomeCutShort = "cut_short"
	outcomeLost     = "lost"
)

func egressOutcome(status string) *string {
	var o string
	switch status {
	case "EGRESS_COMPLETE":
		o = outcomeRecorded
	case "EGRESS_LIMIT_REACHED":
		o = outcomeCutShort
	case "EGRESS_FAILED", "EGRESS_ABORTED":
		o = outcomeLost
	default:
		return nil // still running
	}
	return &o
}

// egressRunning reports whether a status is one an egress passes through before it ends.
func egressRunning(status string) bool {
	return status == "EGRESS_STARTING" || status == "EGRESS_ACTIVE" || status == "EGRESS_ENDING"
}

func (a *API) egress(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	org, proj := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
	st, ok := a.sampledLayer(w, r, store.LayerEgress, "the project's API key has no roomRecord grant, which ListEgress needs")
	if !ok {
		return
	}
	seen, err := a.Layers.EgressSince(ctx, org, proj, a.Now().Add(-ioSince), ioItems)
	if err != nil {
		a.internal(w, r, "egress", err)
		return
	}
	ids := make([]string, len(seen))
	for i, e := range seen {
		ids[i] = e.Egress.ID
	}
	changes, err := a.Layers.EgressChanges(ctx, org, proj, ids)
	if err != nil {
		a.internal(w, r, "egress changes", err)
		return
	}
	hooks, err := a.Layers.EgressWebhooks(ctx, org, proj, ids, a.Now().Add(-hookSince))
	if err != nil {
		a.internal(w, r, "egress webhooks", err)
		return
	}
	byID := histories(changes, hooks)
	items := make([]egressItem, 0, len(seen))
	for _, e := range seen {
		item := egressItem{Egress: e.Egress, Outcome: egressOutcome(e.Egress.Status), History: byID[e.Egress.ID].history}
		if item.Streams == nil {
			item.Streams = []store.EgressStream{}
		}
		if item.History == nil {
			item.History = []store.StatusAt{}
		}
		if first := byID[e.Egress.ID].firstSampled; first != "" {
			// Sampled while it ran, or only after it ended.
			item.Sampled = ptrTo(egressRunning(first))
		}
		items = append(items, item)
	}
	writeJSON(w, http.StatusOK, egressRead{Source: egressSource, Egress: items, Rejected: []rejection{}, SourceStatus: statusOf(st)})
}

// sampledLayer answers 404 for an unknown project and 503 for a layer never read successfully, and
// otherwise returns the layer's status. denied names the grant a key without permission lacks.
func (a *API) sampledLayer(w http.ResponseWriter, r *http.Request, layer, denied string) (store.LayerStatus, bool) {
	ctx := r.Context()
	org, proj := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
	if _, err := a.Projects.Project(ctx, org, proj); err != nil {
		if errors.Is(err, store.ErrNotFound) {
			writeError(w, r, http.StatusNotFound, codeNotFound, "no such project")
			return store.LayerStatus{}, false
		}
		a.internal(w, r, "find project", err)
		return store.LayerStatus{}, false
	}
	st, err := a.Layers.LayerStatus(ctx, org, proj, layer)
	if err != nil {
		a.internal(w, r, layer+" status", err)
		return store.LayerStatus{}, false
	}
	if st.LastGoodReadAt == nil {
		msg := unavailableMessage(st)
		if st.ErrorClass == store.ErrorPermissionDenied {
			msg = denied
		}
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, msg)
		return store.LayerStatus{}, false
	}
	return st, true
}

// itemHistory is one egress or ingress item's status history, and the status of the first read that
// saw it.
type itemHistory struct {
	history      []store.StatusAt
	firstSampled string
}

// histories joins each item's status changes across reads with the statuses its stored webhooks
// reported. Entries are ordered by LiveKit's own time of the change (a sample's updated_at, a
// webhook's update or event time); the worker's read time, or the time a webhook was received,
// stands in when LiveKit gave none and breaks ties. A status equal to the one before is not a
// change, so the earliest evidence of each change is kept.
func histories(changes []store.StatusChange, hooks []store.WebhookStatus) map[string]itemHistory {
	type entry struct {
		order, seen time.Time
		at          *time.Time
		state       string
	}
	entries := map[string][]entry{}
	first := map[string]string{}
	for _, c := range changes {
		read := c.At
		e := entry{order: read, seen: read, at: &read, state: c.Status}
		if c.UpdatedAt != nil {
			e.order, e.at = *c.UpdatedAt, c.UpdatedAt
		}
		entries[c.ID] = append(entries[c.ID], e)
		if _, ok := first[c.ID]; !ok {
			first[c.ID] = c.Status
		}
	}
	for _, h := range hooks {
		e := entry{order: h.ReceivedAt, seen: h.ReceivedAt, at: h.At, state: h.Status}
		if h.At != nil {
			e.order = *h.At
		}
		entries[h.ID] = append(entries[h.ID], e)
	}
	out := make(map[string]itemHistory, len(entries))
	for id, es := range entries {
		slices.SortStableFunc(es, func(a, b entry) int {
			return cmp.Or(a.order.Compare(b.order), a.seen.Compare(b.seen))
		})
		h := itemHistory{history: []store.StatusAt{}, firstSampled: first[id]}
		for _, e := range es {
			if n := len(h.history); n > 0 && h.history[n-1].Status == e.state {
				continue
			}
			h.history = append(h.history, store.StatusAt{At: e.at, Status: e.state})
		}
		out[id] = h
	}
	return out
}

func ptrTo[T any](v T) *T { return &v }
