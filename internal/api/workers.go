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

// Watch limits of the workers read: the samples of the last hour.
const (
	workersSince   = time.Hour
	workersSamples = 50000
)

type workersRead struct {
	Source       string           `json:"source"`
	Workers      []workerItem     `json:"workers"`
	Endpoints    []workerEndpoint `json:"endpoints"`
	SourceStatus sourceStatus     `json:"sourceStatus"`
}

type workerItem struct {
	ID       string `json:"id"`
	Nodename string `json:"nodename"`
	// LoadThreshold is always null: it is a worker option LiveKit Agents does not export.
	LoadThreshold *float64       `json:"loadThreshold"`
	Host          string         `json:"host"`
	EndpointID    string         `json:"endpointId"`
	Samples       []workerSample `json:"samples"`
	SourceStatus  sourceStatus   `json:"sourceStatus"`
}

type workerSample struct {
	T          time.Time `json:"t"`
	Load       *float64  `json:"load"`
	ActiveJobs *int64    `json:"activeJobs"`
}

type workerEndpoint struct {
	ID           string       `json:"id"`
	Host         string       `json:"host"`
	SourceStatus sourceStatus `json:"sourceStatus"`
}

const (
	workersSource = "Read by the worker every 15 s from the Prometheus endpoints this project lists " +
		"(voicegateway project workers add): LiveKit Agents' lk_agents_worker_load and " +
		"lk_agents_active_job_count gauges, per nodename label, parsed with prometheus/common expfmt. " +
		"Idle workers whose endpoint is not listed are not seen, and loadThreshold is a worker option " +
		"LiveKit Agents does not export, so it is null. An endpoint that does not answer is named by its " +
		"error class; its workers keep their earlier samples and gain no new ones."
	workersNone = "This project lists no worker metrics endpoints, so no self-hosted worker is read. " +
		"List one with voicegateway project workers add."
)

func (a *API) workers(w http.ResponseWriter, r *http.Request) {
	// Live snapshots must not be replayed from an HTTP cache.
	w.Header().Set("Cache-Control", "no-store")
	ctx := r.Context()
	org, proj := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
	if _, err := a.Projects.Project(ctx, org, proj); err != nil {
		if errors.Is(err, store.ErrNotFound) {
			writeError(w, r, http.StatusNotFound, codeNotFound, "no such project")
			return
		}
		a.internal(w, r, "find project", err)
		return
	}
	endpoints, err := a.Endpoints.WorkerEndpoints(ctx, org, proj)
	if err != nil {
		a.internal(w, r, "worker endpoints", err)
		return
	}
	st, err := a.Layers.LayerStatus(ctx, org, proj, store.LayerWorkers)
	if err != nil {
		a.internal(w, r, "workers status", err)
		return
	}
	if len(endpoints) == 0 {
		writeJSON(w, http.StatusOK, workersRead{Source: workersNone, Workers: []workerItem{}, Endpoints: []workerEndpoint{}, SourceStatus: statusOf(st)})
		return
	}
	if st.LastGoodReadAt == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, workersUnavailable(st))
		return
	}
	statuses, err := a.Layers.WorkerEndpointStatus(ctx, org, proj)
	if err != nil {
		a.internal(w, r, "worker endpoint status", err)
		return
	}
	samples, err := a.Layers.WorkerSamples(ctx, org, proj, a.Now().Add(-workersSince), workersSamples)
	if err != nil {
		a.internal(w, r, "worker samples", err)
		return
	}
	writeJSON(w, http.StatusOK, workersRead{
		Source:       workersSource,
		Workers:      joinWorkers(endpoints, statuses, samples),
		Endpoints:    endpointsOf(endpoints, statuses),
		SourceStatus: statusOf(st),
	})
}

// joinWorkers groups samples into one worker per endpoint and nodename, each with its endpoint's
// read status. Samples of an endpoint no longer listed are left out.
func joinWorkers(endpoints []store.WorkerEndpoint, statuses map[string]store.LayerStatus, samples []store.WorkerSample) []workerItem {
	hosts := make(map[string]string, len(endpoints))
	for _, e := range endpoints {
		hosts[e.ID] = e.Host
	}
	type key struct{ endpoint, nodename string }
	byKey := map[key]*workerItem{}
	var order []key
	for _, s := range samples {
		host, listed := hosts[s.EndpointID]
		if !listed {
			continue
		}
		k := key{s.EndpointID, s.Nodename}
		item, ok := byKey[k]
		if !ok {
			item = &workerItem{
				ID: s.EndpointID + "/" + s.Nodename, Nodename: s.Nodename, Host: host, EndpointID: s.EndpointID,
				Samples: []workerSample{}, SourceStatus: statusOf(statuses[s.EndpointID]),
			}
			byKey[k] = item
			order = append(order, k)
		}
		item.Samples = append(item.Samples, workerSample{T: s.ReadAt, Load: s.Load, ActiveJobs: s.ActiveJobs})
	}
	out := make([]workerItem, 0, len(order))
	for _, k := range order {
		item := byKey[k]
		slices.SortFunc(item.Samples, func(a, b workerSample) int { return a.T.Compare(b.T) })
		out = append(out, *item)
	}
	slices.SortFunc(out, func(a, b workerItem) int {
		return cmp.Or(cmp.Compare(a.Host, b.Host), cmp.Compare(a.Nodename, b.Nodename), cmp.Compare(a.EndpointID, b.EndpointID))
	})
	return out
}

func endpointsOf(endpoints []store.WorkerEndpoint, statuses map[string]store.LayerStatus) []workerEndpoint {
	out := make([]workerEndpoint, 0, len(endpoints))
	for _, e := range endpoints {
		out = append(out, workerEndpoint{ID: e.ID, Host: e.Host, SourceStatus: statusOf(statuses[e.ID])})
	}
	return out
}

// workersUnavailable names why no listed endpoint has ever answered.
func workersUnavailable(st store.LayerStatus) string {
	if st.LastReadAt == nil {
		return "the listed worker metrics endpoints have not been read yet"
	}
	switch st.ErrorClass {
	case store.ErrorUnreachable:
		return "no listed worker metrics endpoint could be reached"
	case store.ErrorUnauthenticated, store.ErrorPermissionDenied:
		return "the listed worker metrics endpoints refused the read"
	case store.ErrorNotSupported:
		return "the listed endpoints answer without LiveKit Agents worker metrics"
	default:
		return "reading the listed worker metrics endpoints failed (" + st.ErrorClass + ")"
	}
}
