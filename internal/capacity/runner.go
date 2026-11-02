package capacity

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"regexp"
	"strings"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// RoomPrefix starts the name of every room a capacity test creates (decision 0022). Such a room is
// joined like any call, but the call index marks it not servable and leaves it out of the plan's
// minutes (migration 00025, which matches the same shape as testRoom).
const RoomPrefix = "vg-capacity-"

// testRoom is the exact shape of a test room's name, vg-capacity-<8 hex>-<n>, so a customer's own
// room that only starts with the prefix is still a call.
var testRoom = regexp.MustCompile(`^vg-capacity-[0-9a-f]{8}-[0-9]+$`)

// IsTestRoom reports whether a room is one a capacity test created.
func IsTestRoom(name string) bool { return testRoom.MatchString(name) }

// The stop rules' limits (decision 0022, docs/specs/capacity-runner.md).
const (
	// PendingAfter is how long a dispatched job may wait for its agent before the step fails.
	PendingAfter = 10 * time.Second
	// AnswerWithin is how long after its line a call waits for the agent to begin speaking.
	AnswerWithin = 15 * time.Second
	// SlowerBy is how much the step's median answer may exceed the first step's: 50% more.
	SlowerBy = 1.5
	// TesterBusy is the tester's mean CPU over a step above which the step is the tester's limit.
	TesterBusy = 90.0
	// PlanSessions is LiveKit Cloud's Build plan cap on concurrent agent sessions.
	PlanSessions = 5
)

// Runner claims requested capacity runs and runs them, one at a time per worker.
type Runner struct {
	Runs     Runs
	Projects Projects
	Dial     Dial
	CPU      CPU
	// ID names this worker on the runs it holds.
	ID  string
	Log *slog.Logger
	Now func() time.Time
	// Poll is how often the runner looks for a request; Beat how often it heartbeats a run, checks
	// for a stop and samples the tester's CPU; Lost how old a heartbeat may be before any worker
	// ends the run as lost, and Unclaimed how long a request may wait for a worker.
	Poll, Beat, Lost, Unclaimed time.Duration
	// StepFor is how long each step lasts; BusyEvery how often a run checks for a real call.
	StepFor, BusyEvery time.Duration
	// Timeout bounds each LiveKit and storage call.
	Timeout time.Duration
}

// Run looks for a requested run every Poll and runs it, until ctx is cancelled.
func (r *Runner) Run(ctx context.Context) error {
	t := time.NewTicker(r.Poll)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-t.C:
			if err := r.Tick(ctx); err != nil && ctx.Err() == nil {
				r.Log.Error("capacity: poll failed, retrying next period", "err", err)
			}
		}
	}
}

// Tick ends lost runs, then claims and runs the oldest requested one, if any.
func (r *Runner) Tick(ctx context.Context) error {
	now := r.Now()
	cctx, cancel := context.WithTimeout(ctx, r.Timeout)
	lost, err := r.Runs.EndLostCapacityRuns(cctx, now, now.Add(-r.Lost), now.Add(-r.Unclaimed))
	cancel()
	if err != nil {
		return err
	}
	if lost > 0 {
		r.Log.Warn("capacity: ended runs whose runner was lost", "count", lost)
	}
	cctx, cancel = context.WithTimeout(ctx, r.Timeout)
	run, ok, err := r.Runs.ClaimCapacityRun(cctx, r.ID, now)
	cancel()
	if err != nil || !ok {
		return err
	}
	r.Log.Info("capacity: run started", "run", run.ID, "project", run.ProjectID, "agent", run.Agent, "max", run.MaxStep)
	end := (&test{Runner: r, run: run}).do(ctx)
	if ctx.Err() != nil && end.LimitedBy != store.LimitRunnerLost {
		// The worker is stopping: the run did not finish, and its rooms are already gone.
		end = store.CapacityEnd{LimitedBy: store.LimitRunnerLost}
	}
	end.At = r.Now()
	ectx, cancel := context.WithTimeout(context.WithoutCancel(ctx), r.Timeout)
	defer cancel()
	err = r.Runs.EndCapacityRun(ectx, run.ID, r.ID, end)
	if errors.Is(err, store.ErrNotFound) {
		// Another worker ended it as lost while this one ran it.
		return nil
	}
	if err != nil {
		return err
	}
	r.Log.Info("capacity: run ended", "run", run.ID, "limited_by", end.LimitedBy, "capacity", end.Capacity, "error", end.Error)
	return nil
}

// errClass names a LiveKit or storage error for the run's record without its message, which may
// carry a URL or a body.
func errClass(what string, err error) string {
	switch {
	case errors.Is(err, context.DeadlineExceeded):
		return what + "_timeout"
	case errors.Is(err, store.ErrNotFound):
		return what + "_not_found"
	}
	return what
}

func (r *Runner) ctx(ctx context.Context) (context.Context, context.CancelFunc) {
	return context.WithTimeout(ctx, r.Timeout)
}

// roomName is the name of the n-th call of a run: vg-capacity-<first 8 of the run id>-<n>.
func roomName(runID string, n int) string {
	short := strings.ReplaceAll(runID, "-", "")
	if len(short) > 8 {
		short = short[:8]
	}
	return fmt.Sprintf("%s%s-%d", RoomPrefix, short, n)
}
