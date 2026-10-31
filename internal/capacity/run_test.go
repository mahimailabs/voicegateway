package capacity

import (
	"context"
	"io"
	"log/slog"
	"slices"
	"sync"
	"testing"
	"testing/synctest"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// fakeRuns holds one run in memory, as Postgres would for one worker.
type fakeRuns struct {
	mu      sync.Mutex
	run     store.CapacityRun
	claimed bool
	steps   map[int]store.CapacityStep
	end     *store.CapacityEnd
	stopAt  time.Time
	lost    bool
	beats   int
	endedAt time.Time
}

func (f *fakeRuns) ClaimCapacityRun(_ context.Context, _ string, _ time.Time) (store.CapacityRun, bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.claimed {
		return store.CapacityRun{}, false, nil
	}
	f.claimed = true
	return f.run, true, nil
}

func (f *fakeRuns) SetCapacityRunContext(_ context.Context, _, _ string, ms int, cloud *bool) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.run.JoinThresholdMs, f.run.CloudAgent = &ms, cloud
	return nil
}

func (f *fakeRuns) HeartbeatCapacityRun(_ context.Context, _, _ string, now time.Time) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.lost {
		return false, store.ErrNotFound
	}
	f.beats++
	return !f.stopAt.IsZero() && !now.Before(f.stopAt), nil
}

func (f *fakeRuns) PutCapacityStep(_ context.Context, _, _ string, s store.CapacityStep) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	s.JoinSeconds, s.AnswerSeconds = slices.Clone(s.JoinSeconds), slices.Clone(s.AnswerSeconds)
	f.steps[s.Step] = s
	return nil
}

func (f *fakeRuns) EndCapacityRun(_ context.Context, _, _ string, end store.CapacityEnd) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.lost {
		return store.ErrNotFound
	}
	f.end, f.endedAt = &end, time.Now()
	return nil
}

func (f *fakeRuns) EndLostCapacityRuns(context.Context, time.Time, time.Time, time.Time) (int64, error) {
	return 0, nil
}

type fakeProjects struct{}

func (fakeProjects) Project(_ context.Context, org, id string) (store.Project, error) {
	return store.Project{OrganisationID: org, ID: id, LiveKitURL: "ws://livekit.test", LiveKitAPIKey: "key"}, nil
}

func (fakeProjects) LiveKitSecret(context.Context, string, string) (string, error) {
	return "secret", nil
}

func (fakeProjects) ProjectSettings(context.Context, string, string) (store.ProjectSettings, error) {
	return store.ProjectSettings{Thresholds: store.Thresholds{JoinMs: 2000}}, nil
}

// fakeAgent plays a LiveKit server with one agent that takes at most jobs calls at once, joins
// after join, and answers every few seconds in answer(calls up) seconds.
type fakeAgent struct {
	jobs   int
	join   time.Duration
	answer func(active int) float64
	// busy names the rooms, other than the test's, where the agent has a job, as of a time.
	busy  func(now time.Time) []string
	cloud *bool
	// connect is how long a caller takes to join its room.
	connect time.Duration

	mu      sync.Mutex
	rooms   map[string]bool
	created int
	active  int
	callers map[string]chan<- Event
	done    map[string]chan struct{}
	wg      sync.WaitGroup
}

func (a *fakeAgent) CreateRoom(_ context.Context, name string) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.rooms[name] = true
	a.created++
	return nil
}

func (a *fakeAgent) DeleteRoom(_ context.Context, name string) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	delete(a.rooms, name)
	return nil
}

func (a *fakeAgent) Dispatch(_ context.Context, room, _ string) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.active >= a.jobs {
		return nil // refused: the job stays pending
	}
	a.active++
	events, done := a.callers[room], a.done[room]
	a.wg.Add(1)
	go func() {
		defer a.wg.Done()
		wait := a.join
		ev := Event{Room: room, Kind: Joined}
		for {
			select {
			case <-done:
				return
			case <-time.After(wait):
			}
			a.mu.Lock()
			n := a.active
			a.mu.Unlock()
			ev.At = time.Now()
			if ev.Kind == Answered {
				ev.Seconds = a.answer(n)
			}
			select {
			case events <- ev:
			case <-done:
				return
			}
			wait, ev = 4*time.Second, Event{Room: room, Kind: Answered}
		}
	}()
	return nil
}

func (a *fakeAgent) AgentRooms(context.Context, string) ([]string, error) {
	if a.busy == nil {
		return nil, nil
	}
	return a.busy(time.Now()), nil
}

func (a *fakeAgent) CloudAgent(context.Context, string) *bool { return a.cloud }

func (a *fakeAgent) Call(ctx context.Context, room string, events chan<- Event) (func(), error) {
	select {
	case <-time.After(a.connect):
	case <-ctx.Done():
		return nil, ctx.Err()
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	a.callers[room] = events
	done := make(chan struct{})
	a.done[room] = done
	var once sync.Once
	return func() { once.Do(func() { close(done) }) }, nil
}

func (a *fakeAgent) open() int {
	a.mu.Lock()
	defer a.mu.Unlock()
	return len(a.rooms)
}

func steady(int) float64 { return 1.2 }

type setup struct {
	agent *fakeAgent
	runs  *fakeRuns
	cpu   float64
}

// runTest runs one capacity test to its end in a synctest bubble and returns what it stored.
func runTest(t *testing.T, maxStep int, s setup) (*fakeRuns, *fakeAgent) {
	t.Helper()
	a := s.agent
	a.rooms, a.callers, a.done = map[string]bool{}, map[string]chan<- Event{}, map[string]chan struct{}{}
	runs := s.runs
	if runs == nil {
		runs = &fakeRuns{}
	}
	runs.steps = map[int]store.CapacityStep{}
	runs.run = store.CapacityRun{ID: "0b7d8f8e-1111-2222-3333-444455556666", OrganisationID: "o", ProjectID: "p", Agent: "vg-capture", MaxStep: maxStep, State: store.CapacityRunning}
	synctest.Test(t, func(t *testing.T) {
		r := &Runner{
			Runs: runs, Projects: fakeProjects{}, Dial: func(string, string, string) LiveKit { return a },
			CPU: func() (float64, error) { return s.cpu, nil }, ID: "worker-1",
			Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: time.Now,
			Poll: 2 * time.Second, Beat: time.Second, Lost: 30 * time.Second, Unclaimed: 5 * time.Minute,
			StepFor: time.Minute, BusyEvery: 10 * time.Second, Timeout: 10 * time.Second,
		}
		if err := r.Tick(t.Context()); err != nil {
			t.Fatal(err)
		}
		a.wg.Wait()
	})
	return runs, a
}

func outcomes(r *fakeRuns) map[int]string {
	out := map[int]string{}
	for n, s := range r.steps {
		out[n] = s.Outcome
	}
	return out
}

func capacityOf(e *store.CapacityEnd) any {
	if e == nil || e.Capacity == nil {
		return nil
	}
	return *e.Capacity
}

func TestRamp(t *testing.T) {
	yes := true
	tests := []struct {
		name     string
		max      int
		s        setup
		limit    string
		capacity any
		steps    map[int]string
	}{
		{
			name: "a job left pending 10 s ends the step", max: 16,
			s:     setup{agent: &fakeAgent{jobs: 4, join: 500 * time.Millisecond, answer: steady}},
			limit: store.LimitPending, capacity: 4,
			steps: map[int]string{1: store.StepPassed, 2: store.StepPassed, 4: store.StepPassed, 8: store.StepFailed},
		},
		{
			name: "answers more than 50% slower than step 1 end the run", max: 64,
			s: setup{agent: &fakeAgent{jobs: 64, join: 500 * time.Millisecond, answer: func(n int) float64 {
				return map[int]float64{1: 2, 2: 2.5, 4: 3, 8: 4}[n] // 4 is exactly 1.5x step 1: it passes
			}}},
			limit: store.LimitAnswer, capacity: 4,
			steps: map[int]string{1: store.StepPassed, 2: store.StepPassed, 4: store.StepPassed, 8: store.StepFailed},
		},
		{
			name: "every step passes up to the maximum", max: 8,
			s:     setup{agent: &fakeAgent{jobs: 100, join: 500 * time.Millisecond, answer: steady}},
			limit: store.LimitMaxReached, capacity: 8,
			steps: map[int]string{1: store.StepPassed, 2: store.StepPassed, 4: store.StepPassed, 8: store.StepPassed},
		},
		{
			name: "a saturated tester is the tester's limit, not the agent's", max: 16,
			s:     setup{agent: &fakeAgent{jobs: 4, join: 500 * time.Millisecond, answer: steady}, cpu: 95},
			limit: store.LimitTester, capacity: nil,
			steps: map[int]string{1: store.StepInconclusive},
		},
		{
			name: "a Cloud agent refused at 5 sessions is the plan's limit", max: 16,
			s:     setup{agent: &fakeAgent{jobs: 5, join: 500 * time.Millisecond, answer: steady, cloud: &yes}},
			limit: store.LimitPlan, capacity: 4,
			steps: map[int]string{1: store.StepPassed, 2: store.StepPassed, 4: store.StepPassed, 8: store.StepFailed},
		},
		{
			name: "a real call mid-run stops it", max: 16,
			s: setup{agent: &fakeAgent{jobs: 16, join: 500 * time.Millisecond, answer: steady, busy: func(now time.Time) []string {
				if now.Sub(synctestEpoch) > 70*time.Second {
					return []string{"customer-call"}
				}
				return nil
			}}},
			limit: store.LimitRealCall, capacity: 1,
			steps: map[int]string{1: store.StepPassed, 2: store.StepInconclusive},
		},
		{
			name: "a person's stop ends the run within 2 s", max: 16,
			s:     setup{agent: &fakeAgent{jobs: 16, join: 500 * time.Millisecond, answer: steady}, runs: &fakeRuns{stopAt: synctestEpoch.Add(30 * time.Second)}},
			limit: store.LimitStopped, capacity: nil,
			steps: map[int]string{1: store.StepInconclusive},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			runs, a := runTest(t, tt.max, tt.s)
			if runs.end == nil || runs.end.LimitedBy != tt.limit {
				t.Fatalf("end = %+v, want limited by %s", runs.end, tt.limit)
			}
			if got := capacityOf(runs.end); got != tt.capacity {
				t.Errorf("capacity = %v, want %v", got, tt.capacity)
			}
			if got := outcomes(runs); !mapsEqual(got, tt.steps) {
				t.Errorf("steps = %v, want %v", got, tt.steps)
			}
			if n := a.open(); n != 0 {
				t.Errorf("%d test rooms left open", n)
			}
		})
	}
}

// synctestEpoch is the fake clock's start inside every synctest bubble.
var synctestEpoch = time.Date(2000, 1, 1, 0, 0, 0, 0, time.UTC)

func mapsEqual(a, b map[int]string) bool {
	if len(a) != len(b) {
		return false
	}
	for k, v := range a {
		if b[k] != v {
			return false
		}
	}
	return true
}

// TestStopWhileCallsConnect: a step whose callers are slow to join still heartbeats, and a stop
// during it ends the run within 2 s instead of after the connects.
func TestStopWhileCallsConnect(t *testing.T) {
	runs, a := runTest(t, 16, setup{
		agent: &fakeAgent{jobs: 16, join: 500 * time.Millisecond, answer: steady, connect: 5 * time.Minute},
		runs:  &fakeRuns{stopAt: synctestEpoch.Add(45 * time.Second)},
	})
	if runs.end == nil || runs.end.LimitedBy != store.LimitStopped {
		t.Fatalf("end = %+v, want stopped", runs.end)
	}
	if took := runs.endedAt.Sub(synctestEpoch); took > 47*time.Second {
		t.Errorf("stopped after %s, want within 2 s of the stop at 45s", took)
	}
	if runs.beats < 40 {
		t.Errorf("%d heartbeats while connecting, want one a second", runs.beats)
	}
	if n := a.open(); n != 0 {
		t.Errorf("%d test rooms left open", n)
	}
}

func TestPendingStepCountsWhoGotTheAgent(t *testing.T) {
	runs, _ := runTest(t, 16, setup{agent: &fakeAgent{jobs: 4, join: 500 * time.Millisecond, answer: steady}})
	s := runs.steps[8]
	if s.Calls != 8 || s.Joined != 4 || s.Pending != 4 || len(s.JoinSeconds) != 0 {
		t.Errorf("step 8 = calls %d, joined %d, pending %d, joins %v; want 8, 4, 4 and no new joins", s.Calls, s.Joined, s.Pending, s.JoinSeconds)
	}
	if s.EndedAt == nil || s.EndedAt.Sub(s.StartedAt) < PendingAfter || s.EndedAt.Sub(s.StartedAt) > PendingAfter+2*time.Second {
		t.Errorf("step 8 ran %v, want about %v: a pending job ends the step at once", s.EndedAt.Sub(s.StartedAt), PendingAfter)
	}
}

func TestSlowJoinIsLateNotAFailure(t *testing.T) {
	runs, _ := runTest(t, 4, setup{agent: &fakeAgent{jobs: 4, join: 3 * time.Second, answer: steady}})
	if runs.end.LimitedBy != store.LimitMaxReached {
		t.Fatalf("limited by %s, want max_reached: a join under 10 s is not a stop", runs.end.LimitedBy)
	}
	if s := runs.steps[4]; s.Late != 2 || len(s.JoinSeconds) != 2 || s.JoinSeconds[0] != 3 {
		t.Errorf("step 4 = late %d, joins %v; want 2 late joins of 3 s", s.Late, s.JoinSeconds)
	}
}

func TestBusyAgentIsNotTested(t *testing.T) {
	a := &fakeAgent{jobs: 4, answer: steady, busy: func(time.Time) []string { return []string{"customer-call"} }}
	runs, a := runTest(t, 8, setup{agent: a})
	if runs.end.LimitedBy != store.LimitAgentBusy {
		t.Fatalf("limited by %s, want agent_busy", runs.end.LimitedBy)
	}
	if a.created != 0 || len(runs.steps) != 0 {
		t.Errorf("created %d rooms and %d steps for a busy agent, want none", a.created, len(runs.steps))
	}
}

func TestLostRunIsNotEndedTwice(t *testing.T) {
	runs, a := runTest(t, 8, setup{agent: &fakeAgent{jobs: 8, join: 500 * time.Millisecond, answer: steady}, runs: &fakeRuns{lost: true}})
	if runs.end != nil {
		t.Errorf("end = %+v, want none: another worker already ended the run as lost", runs.end)
	}
	if n := a.open(); n != 0 {
		t.Errorf("%d test rooms left open", n)
	}
}

func TestLadder(t *testing.T) {
	if got := Ladder(16); !slices.Equal(got, []int{1, 2, 4, 8, 16}) {
		t.Errorf("Ladder(16) = %v", got)
	}
}
