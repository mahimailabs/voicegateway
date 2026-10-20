package api

import (
	"errors"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

type cloudAgentsRead struct {
	ReadAt       time.Time          `json:"readAt"`
	Source       string             `json:"source"`
	Agents       []store.CloudAgent `json:"agents"`
	SourceStatus sourceStatus       `json:"sourceStatus"`
}

const (
	cloudAgentsSource = "Read by the worker every 60 s with the project's API key: LiveKit Cloud's CloudAgent " +
		"ListAgents and ListAgentVersions (server-sdk-go v2.19.0 AgentClient). Covers agents deployed to " +
		"LiveKit Cloud only; self-hosted workers are read from the metrics endpoints the project lists. " +
		"Agent secrets, version attributes and owners are dropped before storage."
	cloudAgentsNone = "This project's LiveKit server is not LiveKit Cloud, so LiveKit Cloud agents do not apply " +
		"and no Cloud API call is made. Self-hosted workers are read from the metrics endpoints the project lists."
)

func (a *API) cloudAgents(w http.ResponseWriter, r *http.Request) {
	// Live snapshots must not be replayed from an HTTP cache.
	w.Header().Set("Cache-Control", "no-store")
	ctx := r.Context()
	org, proj := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
	st, ok := a.sampledLayer(w, r, store.LayerCloudAgents, "the project's API key has no agent admin grant, which ListAgents needs")
	if !ok {
		return
	}
	got, err := a.Layers.LatestCloudAgents(ctx, org, proj)
	if errors.Is(err, store.ErrNotFound) {
		// The good reads have expired.
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "no Cloud agents read is kept from the last 30 days")
		return
	}
	if err != nil {
		a.internal(w, r, "cloud agents read", err)
		return
	}
	source := cloudAgentsSource
	if !got.Applies {
		source = cloudAgentsNone
	}
	agents := got.Agents
	if agents == nil {
		agents = []store.CloudAgent{}
	}
	writeJSON(w, http.StatusOK, cloudAgentsRead{ReadAt: got.ReadAt, Source: source, Agents: agents, SourceStatus: statusOf(st)})
}
