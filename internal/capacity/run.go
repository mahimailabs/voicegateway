package capacity

import (
	"context"
	"errors"
	"slices"
	"time"

	"golang.org/x/sync/errgroup"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// test is one capacity run as it runs: its calls, and what the steps so far measured.
type test struct {
	*Runner
	run    store.CapacityRun
	lk     LiveKit
	events chan Event
	calls  map[string]*call
	order  []*call
	// late is the join threshold from the project's settings; cloud whether the agent is a LiveKit
	// Cloud agent (nil when unknown); baseline the first step's median answer.
	late     time.Duration
	cloud    *bool
	baseline float64
	capacity *int
}

// call is one synthetic call: its room, when its job was dispatched and when the agent joined.
type call struct {
	room       string
	step       int
	dispatched time.Time
	joined     *time.Time
	gone       bool
	hangup     func()
}

// connectAtOnce bounds how many calls a step connects at the same time.
const connectAtOnce = 8

// do runs the test and returns how it ended; the caller stamps the end time. Every room it created
// is deleted before it returns.
func (t *test) do(ctx context.Context) store.CapacityEnd {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	t.calls = map[string]*call{}
	t.events = make(chan Event, 4*64)
	defer t.cleanup(ctx)

	pctx, pcancel := t.ctx(ctx)
	p, err := t.Projects.Project(pctx, t.run.OrganisationID, t.run.ProjectID)
	var secret string
	if err == nil {
		secret, err = t.Projects.LiveKitSecret(pctx, t.run.OrganisationID, t.run.ProjectID)
	}
	var settings store.ProjectSettings
	if err == nil {
		settings, err = t.Projects.ProjectSettings(pctx, t.run.OrganisationID, t.run.ProjectID)
	}
	pcancel()
	if err != nil {
		return store.CapacityEnd{LimitedBy: store.LimitFailed, Error: errClass("project", err)}
	}
	t.late = time.Duration(settings.Thresholds.JoinMs * float64(time.Millisecond))
	t.lk = t.Dial(p.LiveKitURL, p.LiveKitAPIKey, secret)

	lctx, lcancel := t.ctx(ctx)
	busy, err := t.lk.AgentRooms(lctx, t.run.Agent)
	lcancel()
	if err != nil {
		return store.CapacityEnd{LimitedBy: store.LimitFailed, Error: errClass("livekit_list", err)}
	}
	if len(busy) > 0 {
		return store.CapacityEnd{LimitedBy: store.LimitAgentBusy}
	}
	lctx, lcancel = t.ctx(ctx)
	t.cloud = t.lk.CloudAgent(lctx, t.run.Agent)
	lcancel()
	sctx, scancel := t.ctx(ctx)
	err = t.Runs.SetCapacityRunContext(sctx, t.run.ID, t.ID, int(settings.Thresholds.JoinMs), t.cloud)
	scancel()
	if err != nil {
		return store.CapacityEnd{LimitedBy: store.LimitFailed, Error: errClass("store", err)}
	}

	for _, n := range Ladder(t.run.MaxStep) {
		if end, done := t.step(ctx, n); done {
			end.Capacity = t.capacity
			return end
		}
	}
	return store.CapacityEnd{Capacity: t.capacity, LimitedBy: store.LimitMaxReached}
}

// Ladder is the steps of a run up to max: 1, 2, 4 and so on.
func Ladder(maxStep int) []int {
	var out []int
	for n := 1; n <= maxStep; n *= 2 {
		out = append(out, n)
	}
	return out
}

// step brings the calls up to n, watches them for StepFor and reports whether the run ended.
func (t *test) step(ctx context.Context, n int) (store.CapacityEnd, bool) {
	start := t.Now()
	s := &stepState{CapacityStep: store.CapacityStep{Step: n, StartedAt: start, Outcome: store.StepRunning, JoinSeconds: []float64{}, AnswerSeconds: []float64{}}}
	t.put(ctx, s)
	if end, done := t.add(ctx, n); done {
		return t.end(ctx, s, end)
	}
	t.put(ctx, s)

	stepEnd := time.NewTimer(t.StepFor - t.Now().Sub(start))
	defer stepEnd.Stop()
	beat := time.NewTicker(t.Beat)
	defer beat.Stop()
	lastBusy := t.Now()
	for {
		select {
		case <-ctx.Done():
			return t.end(ctx, s, store.CapacityEnd{LimitedBy: store.LimitRunnerLost})
		case ev := <-t.events:
			if end, done := t.on(s, ev); done {
				return t.end(ctx, s, end)
			}
		case now := <-beat.C:
			if end, done := t.beat(ctx, s, now, &lastBusy); done {
				return t.end(ctx, s, end)
			}
			t.put(ctx, s)
		case <-stepEnd.C:
			t.drain(s)
			return t.close(ctx, s)
		}
	}
}

// stepState is a step as it runs, with the tester's CPU samples.
type stepState struct {
	store.CapacityStep
	cpu []float64
}

// add connects the calls that bring the step to n at once: a room, a caller, then the dispatch, so
// the agent finds the caller waiting. A failed dispatch is the agent's failure; anything else is
// the run's.
func (t *test) add(ctx context.Context, n int) (store.CapacityEnd, bool) {
	fresh := make([]*call, 0, n-len(t.order))
	for i := len(t.order); i < n; i++ {
		c := &call{room: roomName(t.run.ID, i+1), step: n}
		t.calls[c.room] = c
		t.order = append(t.order, c)
		fresh = append(fresh, c)
	}
	actx, cancel := context.WithCancel(ctx)
	defer cancel()
	stopped := make(chan store.CapacityEnd, 1)
	alive := make(chan struct{})
	go func() {
		defer close(alive)
		t.keepAlive(actx, cancel, stopped)
	}()
	var g errgroup.Group
	g.SetLimit(connectAtOnce)
	errs := make([]store.CapacityEnd, len(fresh))
	for i, c := range fresh {
		g.Go(func() error {
			errs[i] = t.connect(actx, c)
			return nil
		})
	}
	_ = g.Wait()
	cancel()
	<-alive
	select {
	case end := <-stopped:
		return end, true
	default:
	}
	for _, e := range errs {
		if e.LimitedBy != "" {
			return e, true
		}
	}
	return store.CapacityEnd{}, false
}

// keepAlive heartbeats the run while a step connects its calls, which can outlast Lost on a large
// step, and cancels the connects when a person stops the run or another worker ended it.
func (t *test) keepAlive(ctx context.Context, cancel context.CancelFunc, stopped chan<- store.CapacityEnd) {
	beat := time.NewTicker(t.Beat)
	defer beat.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case now := <-beat.C:
			hctx, hcancel := t.ctx(ctx)
			stop, err := t.Runs.HeartbeatCapacityRun(hctx, t.run.ID, t.ID, now)
			hcancel()
			switch {
			case errors.Is(err, store.ErrNotFound):
				stopped <- store.CapacityEnd{LimitedBy: store.LimitRunnerLost}
				cancel()
				return
			case err != nil:
				if ctx.Err() == nil {
					t.Log.Warn("capacity: heartbeat failed", "run", t.run.ID, "err", err)
				}
			case stop:
				stopped <- store.CapacityEnd{LimitedBy: store.LimitStopped}
				cancel()
				return
			}
		}
	}
}

func (t *test) connect(ctx context.Context, c *call) store.CapacityEnd {
	cctx, cancel := t.ctx(ctx)
	defer cancel()
	if err := t.lk.CreateRoom(cctx, c.room); err != nil {
		return store.CapacityEnd{LimitedBy: store.LimitFailed, Error: errClass("livekit_create_room", err)}
	}
	hangup, err := t.lk.Call(ctx, c.room, t.events)
	if err != nil {
		return store.CapacityEnd{LimitedBy: store.LimitFailed, Error: errClass("caller_connect", err)}
	}
	c.hangup = hangup
	if err := t.lk.Dispatch(cctx, c.room, t.run.Agent); err != nil {
		return store.CapacityEnd{LimitedBy: store.LimitJobFailed, Error: errClass("livekit_dispatch", err)}
	}
	c.dispatched = t.Now()
	return store.CapacityEnd{}
}

// on applies one call's event to the step.
func (t *test) on(s *stepState, ev Event) (store.CapacityEnd, bool) {
	c, ok := t.calls[ev.Room]
	if !ok || c.gone {
		return store.CapacityEnd{}, false
	}
	switch ev.Kind {
	case Joined:
		if c.joined != nil {
			return store.CapacityEnd{}, false
		}
		at := ev.At
		c.joined = &at
		if c.step == s.Step {
			wait := at.Sub(c.dispatched)
			s.JoinSeconds = append(s.JoinSeconds, wait.Seconds())
			if t.late > 0 && wait > t.late {
				s.Late++
			}
		}
	case Answered:
		if !ev.At.Before(s.StartedAt) {
			s.AnswerSeconds = append(s.AnswerSeconds, ev.Seconds)
		}
	case NoAnswer:
		s.NoAnswer++
		return store.CapacityEnd{LimitedBy: store.LimitAnswer}, true
	case Left:
		c.gone = true
		s.Failed++
		return store.CapacityEnd{LimitedBy: store.LimitJobFailed}, true
	case Lost:
		c.gone = true
		return store.CapacityEnd{LimitedBy: store.LimitFailed, Error: "caller_lost"}, true
	}
	return store.CapacityEnd{}, false
}

// drain applies the events already waiting, so the step's last answers count.
func (t *test) drain(s *stepState) {
	for {
		select {
		case ev := <-t.events:
			if ev.Kind == Joined || ev.Kind == Answered {
				t.on(s, ev)
			}
		default:
			return
		}
	}
}

// beat heartbeats the run, samples the tester's CPU and checks for a stop, a pending job and, every
// BusyEvery, a real call.
func (t *test) beat(ctx context.Context, s *stepState, now time.Time, lastBusy *time.Time) (store.CapacityEnd, bool) {
	hctx, cancel := t.ctx(ctx)
	stop, err := t.Runs.HeartbeatCapacityRun(hctx, t.run.ID, t.ID, now)
	cancel()
	switch {
	case errors.Is(err, store.ErrNotFound):
		return store.CapacityEnd{LimitedBy: store.LimitRunnerLost}, true
	case err != nil:
		t.Log.Warn("capacity: heartbeat failed", "run", t.run.ID, "err", err)
	case stop:
		return store.CapacityEnd{LimitedBy: store.LimitStopped}, true
	}
	if pct, err := t.CPU(); err == nil {
		s.cpu = append(s.cpu, pct)
	}
	for _, c := range t.order {
		if c.joined == nil && !c.dispatched.IsZero() && now.Sub(c.dispatched) >= PendingAfter {
			return store.CapacityEnd{LimitedBy: store.LimitPending}, true
		}
	}
	if now.Sub(*lastBusy) >= t.BusyEvery {
		*lastBusy = now
		bctx, cancel := t.ctx(ctx)
		busy, err := t.lk.AgentRooms(bctx, t.run.Agent)
		cancel()
		switch {
		case err != nil:
			// Unknown, not idle: the next check tries again.
			t.Log.Warn("capacity: real-call check failed", "run", t.run.ID, "err", err)
		case len(busy) > 0:
			return store.CapacityEnd{LimitedBy: store.LimitRealCall}, true
		}
	}
	return store.CapacityEnd{}, false
}

// close ends a step that ran its full time: it passes unless its answers slowed or went unheard.
func (t *test) close(ctx context.Context, s *stepState) (store.CapacityEnd, bool) {
	med, ok := median(s.AnswerSeconds)
	switch {
	case !ok:
		return t.end(ctx, s, store.CapacityEnd{LimitedBy: store.LimitAnswer})
	case s.Step == 1:
		t.baseline = med
	case med > t.baseline*SlowerBy:
		return t.end(ctx, s, store.CapacityEnd{LimitedBy: store.LimitAnswer})
	}
	if testerBusy(s.cpu) {
		return t.end(ctx, s, store.CapacityEnd{LimitedBy: store.LimitTester})
	}
	now := t.Now()
	s.EndedAt = &now
	s.Outcome = store.StepPassed
	t.put(ctx, s)
	n := s.Step
	t.capacity = &n
	return store.CapacityEnd{}, false
}

// end closes a step that a rule or an event stopped. When the tester was saturated, the step says
// so instead of blaming the agent.
func (t *test) end(ctx context.Context, s *stepState, end store.CapacityEnd) (store.CapacityEnd, bool) {
	now := t.Now()
	s.EndedAt = &now
	agentRule := end.LimitedBy == store.LimitPending || end.LimitedBy == store.LimitJobFailed || end.LimitedBy == store.LimitAnswer
	switch {
	case (agentRule || end.LimitedBy == store.LimitTester) && testerBusy(s.cpu):
		end = store.CapacityEnd{LimitedBy: store.LimitTester}
		s.Outcome = store.StepInconclusive
	case agentRule:
		s.Outcome = store.StepFailed
		if end.LimitedBy == store.LimitPending && t.cloud != nil && *t.cloud && t.joinedCount() == PlanSessions {
			end.LimitedBy = store.LimitPlan
		}
	default:
		s.Outcome = store.StepInconclusive
	}
	t.put(ctx, s)
	return end, true
}

// put writes the step as it stands. A failed write is logged: the next one carries the same counts.
func (t *test) put(ctx context.Context, s *stepState) {
	s.Calls = len(t.order)
	s.Joined, s.Pending = 0, 0
	for _, c := range t.order {
		switch {
		case c.gone:
		case c.joined != nil:
			s.Joined++
		default:
			s.Pending++
		}
	}
	if len(s.cpu) > 0 {
		m := mean(s.cpu)
		s.TesterCPUPercent = &m
	}
	pctx, cancel := t.ctx(context.WithoutCancel(ctx))
	defer cancel()
	if err := t.Runs.PutCapacityStep(pctx, t.run.ID, t.ID, s.CapacityStep); err != nil {
		t.Log.Warn("capacity: step write failed", "run", t.run.ID, "step", s.Step, "err", err)
	}
}

func (t *test) joinedCount() int {
	n := 0
	for _, c := range t.order {
		if c.joined != nil && !c.gone {
			n++
		}
	}
	return n
}

// cleanup hangs up every caller and deletes every room the run created, even when the run's
// context is gone. A room it cannot delete closes itself 60 s after it empties.
func (t *test) cleanup(ctx context.Context) {
	if t.lk == nil {
		return
	}
	var g errgroup.Group
	g.SetLimit(connectAtOnce)
	for _, c := range t.order {
		g.Go(func() error {
			if c.hangup != nil {
				c.hangup()
			}
			dctx, cancel := t.ctx(context.WithoutCancel(ctx))
			defer cancel()
			if err := t.lk.DeleteRoom(dctx, c.room); err != nil {
				t.Log.Warn("capacity: test room not deleted; LiveKit closes it once empty", "run", t.run.ID, "room", c.room, "err", err)
			}
			return nil
		})
	}
	_ = g.Wait()
}

func testerBusy(cpu []float64) bool { return len(cpu) > 0 && mean(cpu) > TesterBusy }

func mean(xs []float64) float64 {
	sum := 0.0
	for _, x := range xs {
		sum += x
	}
	return sum / float64(len(xs))
}

func median(xs []float64) (float64, bool) {
	if len(xs) == 0 {
		return 0, false
	}
	s := slices.Clone(xs)
	slices.Sort(s)
	if len(s)%2 == 1 {
		return s[len(s)/2], true
	}
	return (s[len(s)/2-1] + s[len(s)/2]) / 2, true
}
