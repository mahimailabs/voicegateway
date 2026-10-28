package pgstore

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func capacityStore(t *testing.T) *Store {
	t.Helper()
	s, _ := migrated(t)
	if _, err := s.CreateProject(context.Background(), project("p", "APIp")); err != nil {
		t.Fatal(err)
	}
	return s
}

func request(at time.Time) store.NewCapacityRun {
	return store.NewCapacityRun{OrganisationID: "default", ProjectID: "p", Agent: "vg-capture", MaxStep: 16, RequestedBy: "u1", RequestedAt: at}
}

func TestOneRunPerProject(t *testing.T) {
	s := capacityStore(t)
	ctx := context.Background()
	now := time.Date(2026, 10, 28, 12, 0, 0, 0, time.UTC)
	first, err := s.CreateCapacityRun(ctx, request(now))
	if err != nil {
		t.Fatal(err)
	}
	if first.State != store.CapacityRequested || first.Capacity != nil || first.LimitedBy != nil {
		t.Errorf("new run = %+v, want requested with nothing measured", first)
	}
	if _, err := s.CreateCapacityRun(ctx, request(now)); !errors.Is(err, store.ErrRunActive) {
		t.Fatalf("second open run: %v, want ErrRunActive", err)
	}
	unknown := request(now)
	unknown.ProjectID = "nope"
	if _, err := s.CreateCapacityRun(ctx, unknown); !errors.Is(err, store.ErrNotFound) {
		t.Errorf("run for an unknown project: %v, want ErrNotFound", err)
	}
	// Once the first ends, the project may run another.
	if _, ok, err := s.ClaimCapacityRun(ctx, "w1", now); err != nil || !ok {
		t.Fatalf("claim: %v, %v", ok, err)
	}
	c := 4
	if err := s.EndCapacityRun(ctx, first.ID, "w1", store.CapacityEnd{At: now, Capacity: &c, LimitedBy: store.LimitPending}); err != nil {
		t.Fatal(err)
	}
	if _, err := s.CreateCapacityRun(ctx, request(now.Add(time.Minute))); err != nil {
		t.Errorf("run after the first ended: %v", err)
	}
}

func TestClaimHeartbeatStepsAndEnd(t *testing.T) {
	s := capacityStore(t)
	ctx := context.Background()
	now := time.Date(2026, 10, 28, 12, 0, 0, 0, time.UTC)
	run, err := s.CreateCapacityRun(ctx, request(now))
	if err != nil {
		t.Fatal(err)
	}
	got, ok, err := s.ClaimCapacityRun(ctx, "w1", now)
	if err != nil || !ok || got.ID != run.ID || got.State != store.CapacityRunning {
		t.Fatalf("claim = %+v, %v, %v", got, ok, err)
	}
	if _, ok, err := s.ClaimCapacityRun(ctx, "w2", now); err != nil || ok {
		t.Fatalf("second claim = %v, %v; want nothing left to claim", ok, err)
	}
	if _, err := s.HeartbeatCapacityRun(ctx, run.ID, "w2", now); !errors.Is(err, store.ErrNotFound) {
		t.Errorf("heartbeat from another worker: %v, want ErrNotFound", err)
	}
	yes := true
	if err := s.SetCapacityRunContext(ctx, run.ID, "w1", 2000, &yes); err != nil {
		t.Fatal(err)
	}
	cpu := 41.5
	step := store.CapacityStep{
		Step: 1, StartedAt: now, Outcome: store.StepRunning, Calls: 1, Joined: 1,
		JoinSeconds: []float64{0.52}, AnswerSeconds: []float64{3.68, 3.5}, TesterCPUPercent: &cpu,
	}
	if err := s.PutCapacityStep(ctx, run.ID, "w1", step); err != nil {
		t.Fatal(err)
	}
	ended := now.Add(time.Minute)
	step.Outcome, step.EndedAt = store.StepPassed, &ended
	if err := s.PutCapacityStep(ctx, run.ID, "w1", step); err != nil {
		t.Fatal(err)
	}
	// A step from a worker that no longer holds the run is not written.
	if err := s.PutCapacityStep(ctx, run.ID, "w2", store.CapacityStep{Step: 2, StartedAt: now, Outcome: store.StepRunning}); err != nil {
		t.Fatal(err)
	}
	if _, err := s.StopCapacityRun(ctx, "default", "p", run.ID, now); err != nil {
		t.Fatal(err)
	}
	if stop, err := s.HeartbeatCapacityRun(ctx, run.ID, "w1", now); err != nil || !stop {
		t.Errorf("heartbeat after a stop = %v, %v; want stop", stop, err)
	}
	c := 1
	if err := s.EndCapacityRun(ctx, run.ID, "w1", store.CapacityEnd{At: ended, Capacity: &c, LimitedBy: store.LimitStopped}); err != nil {
		t.Fatal(err)
	}
	latest, err := s.LatestCapacityRun(ctx, "default", "p", "vg-capture")
	if err != nil {
		t.Fatal(err)
	}
	if latest.State != store.CapacityEnded || *latest.LimitedBy != store.LimitStopped || *latest.Capacity != 1 || !*latest.CloudAgent || *latest.JoinThresholdMs != 2000 {
		t.Errorf("latest = %+v", latest)
	}
	if len(latest.Steps) != 1 || latest.Steps[0].Outcome != store.StepPassed || len(latest.Steps[0].AnswerSeconds) != 2 || *latest.Steps[0].TesterCPUPercent != cpu {
		t.Errorf("steps = %+v, want step 1 passed with its two answers", latest.Steps)
	}
	if _, err := s.LatestCapacityRun(ctx, "default", "p", "another-agent"); !errors.Is(err, store.ErrNotFound) {
		t.Errorf("another agent's run: %v, want ErrNotFound", err)
	}
}

func TestLostRunnerEndsTheRun(t *testing.T) {
	s := capacityStore(t)
	ctx := context.Background()
	now := time.Date(2026, 10, 28, 12, 0, 0, 0, time.UTC)
	run, err := s.CreateCapacityRun(ctx, request(now))
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := s.ClaimCapacityRun(ctx, "w1", now); err != nil {
		t.Fatal(err)
	}
	later := now.Add(31 * time.Second)
	if n, err := s.EndLostCapacityRuns(ctx, later, later.Add(-30*time.Second), later.Add(-5*time.Minute)); err != nil || n != 1 {
		t.Fatalf("ended %d lost runs, %v; want 1", n, err)
	}
	if err := s.EndCapacityRun(ctx, run.ID, "w1", store.CapacityEnd{At: later, LimitedBy: store.LimitMaxReached}); !errors.Is(err, store.ErrNotFound) {
		t.Errorf("the lost runner's own end: %v, want ErrNotFound", err)
	}
	got, err := s.CapacityRun(ctx, run.ID)
	if err != nil || *got.LimitedBy != store.LimitRunnerLost {
		t.Errorf("run = %+v, %v; want runner_lost", got, err)
	}

	// A request no worker claims within the window ends too, so the project is not blocked.
	waiting, err := s.CreateCapacityRun(ctx, request(later))
	if err != nil {
		t.Fatal(err)
	}
	if n, err := s.EndLostCapacityRuns(ctx, later.Add(6*time.Minute), later, later.Add(time.Minute)); err != nil || n != 1 {
		t.Fatalf("ended %d unclaimed runs, %v; want 1", n, err)
	}
	if got, _ := s.CapacityRun(ctx, waiting.ID); got.State != store.CapacityEnded {
		t.Errorf("unclaimed run state = %s, want ended", got.State)
	}
}

func TestStopBeforeAWorkerClaimsIt(t *testing.T) {
	s := capacityStore(t)
	ctx := context.Background()
	now := time.Date(2026, 10, 28, 12, 0, 0, 0, time.UTC)
	run, err := s.CreateCapacityRun(ctx, request(now))
	if err != nil {
		t.Fatal(err)
	}
	got, err := s.StopCapacityRun(ctx, "default", "p", run.ID, now)
	if err != nil || got.State != store.CapacityEnded || *got.LimitedBy != store.LimitStopped {
		t.Fatalf("stop = %+v, %v; want ended as stopped", got, err)
	}
	if _, err := s.StopCapacityRun(ctx, "default", "p", run.ID, now); !errors.Is(err, store.ErrNotFound) {
		t.Errorf("stopping an ended run: %v, want ErrNotFound", err)
	}
	if _, err := s.StopCapacityRun(ctx, "default", "other", run.ID, now); !errors.Is(err, store.ErrNotFound) {
		t.Errorf("stopping through another project: %v, want ErrNotFound", err)
	}
}

// TestCapacityRoomsAreNotCalls: a capacity test's room is joined like any call but never served,
// and never counts toward the organisation's usage.
func TestCapacityRoomsAreNotCalls(t *testing.T) {
	s := capacityStore(t)
	ctx := context.Background()
	start := time.Date(2026, 10, 28, 12, 0, 0, 0, time.UTC)
	end := start.Add(time.Minute)
	for _, room := range []string{"vg-capacity-0b7d8f8e-1", "customer-call"} {
		id, err := s.UpsertCall(ctx, store.CallUpsert{OrganisationID: "default", ProjectID: "p", RoomSID: "RM_" + room, RoomName: room, StartedAt: &start, LastActivityAt: &start, FirstSeenAt: &start})
		if err != nil {
			t.Fatal(err)
		}
		if err := s.FinishCall(ctx, id, &end); err != nil {
			t.Fatal(err)
		}
	}
	first, err := s.FirstCall(ctx, "default", "p")
	if err != nil {
		t.Fatal(err)
	}
	var room string
	if err := s.pool.QueryRow(ctx, `SELECT room_name FROM calls WHERE id = $1`, first.ID).Scan(&room); err != nil || room != "customer-call" {
		t.Errorf("first call room = %q, %v; want customer-call, never the test room", room, err)
	}
	var servable int
	if err := s.pool.QueryRow(ctx, `SELECT count(*) FROM call_list WHERE servable`).Scan(&servable); err != nil || servable != 1 {
		t.Errorf("servable calls = %d, %v; want 1", servable, err)
	}
	u, err := s.OrganisationUsage(ctx, "default", start.Add(-time.Hour), start.Add(time.Hour))
	if err != nil {
		t.Fatal(err)
	}
	if u.Calls != 1 || u.CallSeconds != 60 {
		t.Errorf("usage = %d calls, %d s; want the customer's call alone", u.Calls, u.CallSeconds)
	}
}
