package api

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// codeRunActive is the contract's run_active: the project already has an open capacity test.
const codeRunActive = "run_active"

// maxCapacityBody bounds a startCapacityRun body: an agent name and two small fields.
const maxCapacityBody = 1 << 10

// capacityRun is the contract's CapacityRun.
type capacityRun struct {
	ID              string         `json:"id"`
	Agent           string         `json:"agent"`
	MaxStep         int            `json:"maxStep"`
	State           string         `json:"state"`
	RequestedAt     time.Time      `json:"requestedAt"`
	StartedAt       *time.Time     `json:"startedAt"`
	EndedAt         *time.Time     `json:"endedAt"`
	StopRequested   bool           `json:"stopRequested"`
	Capacity        *int           `json:"capacity"`
	LimitedBy       *string        `json:"limitedBy"`
	CloudAgent      *bool          `json:"cloudAgent"`
	JoinThresholdMs *int           `json:"joinThresholdMs"`
	Error           *string        `json:"error"`
	Steps           []capacityStep `json:"steps"`
}

// capacityStep is the contract's CapacityStep. The counts are pointers only because the contract
// also carries a captured run that lacked them; the runner always reports them.
type capacityStep struct {
	Step             int        `json:"step"`
	StartedAt        time.Time  `json:"startedAt"`
	EndedAt          *time.Time `json:"endedAt"`
	Outcome          string     `json:"outcome"`
	Calls            *int       `json:"calls"`
	Joined           *int       `json:"joined"`
	Pending          *int       `json:"pending"`
	Failed           *int       `json:"failed"`
	Late             *int       `json:"late"`
	NoAnswer         *int       `json:"noAnswer"`
	JoinSeconds      []float64  `json:"joinSeconds"`
	AnswerSeconds    []float64  `json:"answerSeconds"`
	TesterCPUPercent *float64   `json:"testerCpuPercent"`
}

func toCapacityRun(r store.CapacityRun) capacityRun {
	out := capacityRun{
		ID: r.ID, Agent: r.Agent, MaxStep: r.MaxStep, State: r.State, RequestedAt: r.RequestedAt,
		StartedAt: r.StartedAt, EndedAt: r.EndedAt, StopRequested: r.StopRequested, Capacity: r.Capacity,
		LimitedBy: r.LimitedBy, CloudAgent: r.CloudAgent, JoinThresholdMs: r.JoinThresholdMs, Error: r.Error,
		Steps: make([]capacityStep, 0, len(r.Steps)),
	}
	for _, s := range r.Steps {
		out.Steps = append(out.Steps, capacityStep{
			Step: s.Step, StartedAt: s.StartedAt, EndedAt: s.EndedAt, Outcome: s.Outcome,
			Calls: &s.Calls, Joined: &s.Joined, Pending: &s.Pending, Failed: &s.Failed, Late: &s.Late, NoAnswer: &s.NoAnswer,
			JoinSeconds: nonNilSeconds(s.JoinSeconds), AnswerSeconds: nonNilSeconds(s.AnswerSeconds), TesterCPUPercent: s.TesterCPUPercent,
		})
	}
	return out
}

func nonNilSeconds(xs []float64) []float64 {
	if xs == nil {
		return []float64{}
	}
	return xs
}

// agentName is the one rule for the agent a capacity test dispatches: LiveKit's agent_name, 1 to
// 256 bytes with no control characters.
func agentName(s string) (string, bool) {
	if s == "" || len(s) > 256 || !utf8.ValidString(s) || strings.ContainsFunc(s, unicode.IsControl) {
		return "", false
	}
	return s, true
}

// getCapacity serves operationId getCapacity: the agent's latest run, or null before its first.
func (a *API) getCapacity(w http.ResponseWriter, r *http.Request) {
	if a.Capacity == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "capacity tests are not available on this server")
		return
	}
	agent, ok := agentName(r.URL.Query().Get("agent"))
	if !ok {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "agent must be the agent's name, 1 to 256 bytes")
		return
	}
	org, proj := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
	if _, err := a.Projects.Project(r.Context(), org, proj); err != nil {
		a.failWith(w, r, err, "project not found", "the capacity test could not be read")
		return
	}
	run, err := a.Capacity.LatestCapacityRun(r.Context(), org, proj, agent)
	if errors.Is(err, store.ErrNotFound) {
		writeJSON(w, http.StatusOK, nil)
		return
	}
	if err != nil {
		a.failWith(w, r, err, "", "the capacity test could not be read")
		return
	}
	writeJSON(w, http.StatusOK, toCapacityRun(run))
}

// startCapacityRun serves operationId startCapacityRun. It only stores the request; a worker runs
// it (web never dials LiveKit for a test).
func (a *API) startCapacityRun(w http.ResponseWriter, r *http.Request) {
	if a.Capacity == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "capacity tests are not available on this server")
		return
	}
	var body struct {
		Agent   string `json:"agent"`
		MaxStep int    `json:"maxStep"`
		Consent bool   `json:"consent"`
	}
	dec := json.NewDecoder(io.LimitReader(r.Body, maxCapacityBody))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&body); err != nil {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "the body must be agent, maxStep and consent")
		return
	}
	agent, ok := agentName(body.Agent)
	switch {
	case !ok:
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "agent must be the agent's name, 1 to 256 bytes; an agent without a name cannot be tested")
		return
	case body.MaxStep != 8 && body.MaxStep != 16 && body.MaxStep != 32 && body.MaxStep != 64:
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "maxStep must be 8, 16, 32 or 64")
		return
	case !body.Consent:
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "consent must be true: the test places real calls through the agent's providers")
		return
	}
	org, proj := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
	p, _ := store.PrincipalFrom(r.Context())
	run, err := a.Capacity.CreateCapacityRun(r.Context(), store.NewCapacityRun{
		OrganisationID: org, ProjectID: proj, Agent: agent, MaxStep: body.MaxStep, RequestedBy: p.UserID, RequestedAt: a.Now(),
	})
	if errors.Is(err, store.ErrRunActive) {
		writeError(w, r, http.StatusConflict, codeRunActive, "this project already has a capacity test running; stop it or wait for it to end")
		return
	}
	if err != nil {
		a.failWith(w, r, err, "project not found", "the capacity test could not be started")
		return
	}
	writeJSON(w, http.StatusAccepted, toCapacityRun(run))
}

// stopCapacityRun serves operationId stopCapacityRun.
func (a *API) stopCapacityRun(w http.ResponseWriter, r *http.Request) {
	if a.Capacity == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "capacity tests are not available on this server")
		return
	}
	run, err := a.Capacity.StopCapacityRun(r.Context(), chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId"), chi.URLParam(r, "runId"), a.Now())
	if err != nil {
		a.failWith(w, r, err, "no capacity test running with this id in this project", "the capacity test could not be stopped")
		return
	}
	writeJSON(w, http.StatusOK, toCapacityRun(run))
}
