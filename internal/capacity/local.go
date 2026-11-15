package capacity

import (
	"context"
	"slices"
	"sync"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Local holds one capacity run in memory, for `voicegateway capacity`: the same runner, ramp, stop
// rules and cleanup as a worker's run, with no database (docs/specs/capacity-command.md). It is the
// run's store and its project.
type Local struct {
	// LiveKitURL, APIKey and APISecret reach the agent's LiveKit project; JoinMs is the join
	// threshold above which a join counts as late.
	LiveKitURL, APIKey, APISecret string
	JoinMs                        float64
	// OnStep, when set, sees every step write, in order.
	OnStep func(store.CapacityStep)

	mu      sync.Mutex
	run     store.CapacityRun
	claimed bool
	stop    bool
	steps   []store.CapacityStep
}

// NewLocal returns a local store holding one requested run of the agent up to maxStep calls.
func NewLocal(id, agent string, maxStep int, now time.Time) *Local {
	return &Local{run: store.CapacityRun{
		ID: id, OrganisationID: "local", ProjectID: "local", Agent: agent, MaxStep: maxStep,
		RequestedAt: now, State: store.CapacityRequested,
	}}
}

// Stop asks the run to stop: the runner sees it on its next beat, ends the run as stopped and
// deletes every room it created.
func (l *Local) Stop() {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.stop = true
	l.run.StopRequested = true
}

// Run is the run as it stands, with its steps in the order they ran.
func (l *Local) Run() store.CapacityRun {
	l.mu.Lock()
	defer l.mu.Unlock()
	r := l.run
	r.Steps = slices.Clone(l.steps)
	return r
}

func (l *Local) ClaimCapacityRun(_ context.Context, _ string, now time.Time) (store.CapacityRun, bool, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.claimed {
		return store.CapacityRun{}, false, nil
	}
	l.claimed = true
	l.run.State, l.run.StartedAt = store.CapacityRunning, &now
	return l.run, true, nil
}

func (l *Local) SetCapacityRunContext(_ context.Context, _, _ string, joinThresholdMs int, cloudAgent *bool) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.run.JoinThresholdMs, l.run.CloudAgent = &joinThresholdMs, cloudAgent
	return nil
}

func (l *Local) HeartbeatCapacityRun(context.Context, string, string, time.Time) (bool, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.stop, nil
}

// PutCapacityStep replaces the step's last write, or adds it after the steps so far.
func (l *Local) PutCapacityStep(_ context.Context, _, _ string, s store.CapacityStep) error {
	s.JoinSeconds, s.AnswerSeconds = slices.Clone(s.JoinSeconds), slices.Clone(s.AnswerSeconds)
	l.mu.Lock()
	i := slices.IndexFunc(l.steps, func(x store.CapacityStep) bool { return x.Step == s.Step })
	if i < 0 {
		l.steps = append(l.steps, s)
	} else {
		l.steps[i] = s
	}
	on := l.OnStep
	l.mu.Unlock()
	if on != nil {
		on(s)
	}
	return nil
}

func (l *Local) EndCapacityRun(_ context.Context, _, _ string, end store.CapacityEnd) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	at, limit := end.At, end.LimitedBy
	l.run.State, l.run.EndedAt, l.run.Capacity, l.run.LimitedBy = store.CapacityEnded, &at, end.Capacity, &limit
	if end.Error != "" {
		e := end.Error
		l.run.Error = &e
	}
	return nil
}

// EndLostCapacityRuns has nothing to end: a local run lives and dies with the command.
func (l *Local) EndLostCapacityRuns(context.Context, time.Time, time.Time, time.Time) (int64, error) {
	return 0, nil
}

func (l *Local) Project(_ context.Context, org, id string) (store.Project, error) {
	return store.Project{OrganisationID: org, ID: id, LiveKitURL: l.LiveKitURL, LiveKitAPIKey: l.APIKey}, nil
}

func (l *Local) LiveKitSecret(context.Context, string, string) (string, error) {
	return l.APISecret, nil
}

func (l *Local) ProjectSettings(context.Context, string, string) (store.ProjectSettings, error) {
	return store.ProjectSettings{Thresholds: store.Thresholds{JoinMs: l.JoinMs}}, nil
}
