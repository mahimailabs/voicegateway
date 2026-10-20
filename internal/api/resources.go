package api

import (
	"context"
	"errors"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

type ResourceEndpoints interface {
	ResourceEndpoints(context.Context, string, string) ([]store.ResourceEndpoint, error)
}
type ResourceSamples interface {
	ResourceSamples(context.Context, string, string, time.Time, int) ([]store.ResourceSample, error)
	ResourceEndpointStatus(context.Context, string, string) (map[string]store.LayerStatus, error)
}
type resourceSample struct {
	T                time.Time `json:"t"`
	ObservedAt       time.Time `json:"observedAt"`
	CPUUsed          *float64  `json:"cpuUsed"`
	CPUCores         *float64  `json:"cpuCores"`
	MemoryBytes      *float64  `json:"memoryBytes"`
	MemoryLimitBytes *float64  `json:"memoryLimitBytes"`
}
type resourceItem struct {
	ID           string           `json:"id"`
	Kind         string           `json:"kind"`
	Target       string           `json:"target"`
	ContainerID  string           `json:"containerId"`
	Host         string           `json:"host"`
	SourceStatus sourceStatus     `json:"sourceStatus"`
	Samples      []resourceSample `json:"samples"`
}
type resourcesRead struct {
	Source    string         `json:"source"`
	Resources []resourceItem `json:"resources"`
}

func (a *API) resources(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	org, project := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
	if _, err := a.Projects.Project(r.Context(), org, project); err != nil {
		if errors.Is(err, store.ErrNotFound) {
			writeError(w, r, http.StatusNotFound, codeNotFound, "no such project")
		} else {
			a.internal(w, r, "find project", err)
		}
		return
	}
	if a.ResourceEndpoints == nil || a.ResourceSamples == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "resource collection unavailable")
		return
	}
	endpoints, err := a.ResourceEndpoints.ResourceEndpoints(r.Context(), org, project)
	if err != nil {
		a.internal(w, r, "resource endpoints", err)
		return
	}
	out := resourcesRead{Source: "cAdvisor container CPU and memory, including child processes. CPU used is measured cores; worker load is separate. Unconfigured containers and hosted infrastructure remain unknown.", Resources: []resourceItem{}}
	if len(endpoints) == 0 {
		writeJSON(w, http.StatusOK, out)
		return
	}
	statuses, err := a.ResourceSamples.ResourceEndpointStatus(r.Context(), org, project)
	if err != nil {
		a.internal(w, r, "resource status", err)
		return
	}
	samples, err := a.ResourceSamples.ResourceSamples(r.Context(), org, project, a.Now().Add(-time.Hour), 50000)
	if err != nil {
		a.internal(w, r, "resource samples", err)
		return
	}
	byEndpoint := make(map[string][]resourceSample)
	for _, sample := range samples {
		byEndpoint[sample.EndpointID] = append(byEndpoint[sample.EndpointID], resourceSample{T: sample.ReadAt, ObservedAt: sample.ObservedAt, CPUUsed: sample.CPUUsed, CPUCores: sample.CPUCores, MemoryBytes: sample.MemoryBytes, MemoryLimitBytes: sample.MemoryLimitBytes})
	}
	for _, endpoint := range endpoints {
		values := byEndpoint[endpoint.ID]
		if values == nil {
			values = []resourceSample{}
		}
		out.Resources = append(out.Resources, resourceItem{ID: endpoint.ID, Kind: endpoint.Kind, Target: endpoint.Target, ContainerID: endpoint.ContainerID, Host: endpoint.Host, SourceStatus: statusOf(statuses[endpoint.ID]), Samples: values})
	}
	writeJSON(w, http.StatusOK, out)
}
