package pgstore

import (
	"context"
	"errors"
	"fmt"
	"math"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// CreateCapacityRun stores a requested capacity test and returns it. It returns store.ErrRunActive
// when the project already has a run that has not ended, and store.ErrNotFound for an unknown project.
func (s *Store) CreateCapacityRun(ctx context.Context, n store.NewCapacityRun) (store.CapacityRun, error) {
	id, err := s.q.CreateCapacityRun(ctx, CreateCapacityRunParams{
		OrganisationID: n.OrganisationID, ProjectID: n.ProjectID, Agent: n.Agent,
		MaxStep: int32(min(n.MaxStep, math.MaxInt32)), //nolint:gosec // bounded above
		RequestedBy: text(n.RequestedBy), RequestedAt: at(n.RequestedAt),
	})
	var pgErr *pgconn.PgError
	switch {
	case errors.As(err, &pgErr) && pgErr.Code == uniqueViolation:
		return store.CapacityRun{}, store.ErrRunActive
	case errors.As(err, &pgErr) && pgErr.Code == foreignKeyViolation:
		return store.CapacityRun{}, store.ErrNotFound
	case err != nil:
		return store.CapacityRun{}, fmt.Errorf("create capacity run: %w", err)
	}
	return s.capacityRun(ctx, id)
}

// ClaimCapacityRun takes the oldest requested run for runner, or reports false when none waits.
func (s *Store) ClaimCapacityRun(ctx context.Context, runner string, now time.Time) (store.CapacityRun, bool, error) {
	id, err := s.q.ClaimCapacityRun(ctx, ClaimCapacityRunParams{Runner: text(runner), Now: at(now)})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.CapacityRun{}, false, nil
	}
	if err != nil {
		return store.CapacityRun{}, false, fmt.Errorf("claim capacity run: %w", err)
	}
	r, err := s.capacityRun(ctx, id)
	return r, err == nil, err
}

// SetCapacityRunContext records the join threshold a run classifies late joins with, and whether
// its agent is a LiveKit Cloud agent (nil when the run could not tell).
func (s *Store) SetCapacityRunContext(ctx context.Context, id, runner string, joinThresholdMs int, cloudAgent *bool) error {
	u, err := uuid(id)
	if err != nil {
		return err
	}
	cloud := pgtype.Bool{}
	if cloudAgent != nil {
		cloud = pgtype.Bool{Bool: *cloudAgent, Valid: true}
	}
	if err := s.q.SetCapacityRunContext(ctx, SetCapacityRunContextParams{
		JoinThresholdMs: pgtype.Int4{Int32: i32(joinThresholdMs), Valid: true},
		CloudAgent:      cloud, ID: u, Runner: text(runner),
	}); err != nil {
		return fmt.Errorf("set capacity run context: %w", err)
	}
	return nil
}

// HeartbeatCapacityRun keeps runner's hold on a running run and reports whether a person asked it
// to stop. It returns store.ErrNotFound when the run is no longer runner's, such as when another
// worker ended it as lost.
func (s *Store) HeartbeatCapacityRun(ctx context.Context, id, runner string, now time.Time) (bool, error) {
	u, err := uuid(id)
	if err != nil {
		return false, err
	}
	stop, err := s.q.HeartbeatCapacityRun(ctx, HeartbeatCapacityRunParams{Now: at(now), ID: u, Runner: text(runner)})
	if errors.Is(err, pgx.ErrNoRows) {
		return false, store.ErrNotFound
	}
	if err != nil {
		return false, fmt.Errorf("heartbeat capacity run: %w", err)
	}
	return stop, nil
}

// PutCapacityStep writes one step of runner's run as it stands; it writes nothing once the run is
// no longer runner's.
func (s *Store) PutCapacityStep(ctx context.Context, id, runner string, st store.CapacityStep) error {
	u, err := uuid(id)
	if err != nil {
		return err
	}
	cpu := pgtype.Float8{}
	if st.TesterCPUPercent != nil {
		cpu = pgtype.Float8{Float64: *st.TesterCPUPercent, Valid: true}
	}
	ended := pgtype.Timestamptz{}
	if st.EndedAt != nil {
		ended = at(*st.EndedAt)
	}
	if err := s.q.PutCapacityStep(ctx, PutCapacityStepParams{
		RunID: u, Runner: text(runner), Step: i32(st.Step), StartedAt: at(st.StartedAt), EndedAt: ended,
		Outcome: st.Outcome, Calls: i32(st.Calls), Joined: i32(st.Joined), Pending: i32(st.Pending),
		Failed: i32(st.Failed), Late: i32(st.Late), JoinSeconds: nonNil(st.JoinSeconds),
		AnswerSeconds: nonNil(st.AnswerSeconds), NoAnswer: i32(st.NoAnswer), TesterCpuPercent: cpu,
	}); err != nil {
		return fmt.Errorf("put capacity step: %w", err)
	}
	return nil
}

// EndCapacityRun ends runner's run. It returns store.ErrNotFound when the run is no longer runner's.
func (s *Store) EndCapacityRun(ctx context.Context, id, runner string, end store.CapacityEnd) error {
	u, err := uuid(id)
	if err != nil {
		return err
	}
	capacity := pgtype.Int4{}
	if end.Capacity != nil {
		capacity = pgtype.Int4{Int32: i32(*end.Capacity), Valid: true}
	}
	n, err := s.q.EndCapacityRun(ctx, EndCapacityRunParams{
		EndedAt: at(end.At), Capacity: capacity, LimitedBy: text(end.LimitedBy), Error: text(end.Error),
		ID: u, Runner: text(runner),
	})
	if err != nil {
		return fmt.Errorf("end capacity run: %w", err)
	}
	if n == 0 {
		return store.ErrNotFound
	}
	return nil
}

// EndLostCapacityRuns ends, as runner_lost, every running run whose heartbeat is older than
// staleBefore and every request no worker claimed before unclaimedBefore.
func (s *Store) EndLostCapacityRuns(ctx context.Context, now, staleBefore, unclaimedBefore time.Time) (int64, error) {
	n, err := s.q.EndLostCapacityRuns(ctx, EndLostCapacityRunsParams{Now: at(now), StaleBefore: at(staleBefore), UnclaimedBefore: at(unclaimedBefore)})
	if err != nil {
		return 0, fmt.Errorf("end lost capacity runs: %w", err)
	}
	return n, nil
}

// StopCapacityRun asks a project's open run to stop; a run no worker has claimed yet ends at once.
// It returns store.ErrNotFound when the project has no such open run.
func (s *Store) StopCapacityRun(ctx context.Context, org, project, id string, now time.Time) (store.CapacityRun, error) {
	u, err := uuid(id)
	if err != nil {
		return store.CapacityRun{}, store.ErrNotFound
	}
	got, err := s.q.StopCapacityRun(ctx, StopCapacityRunParams{OrganisationID: org, ProjectID: project, ID: u, Now: at(now)})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.CapacityRun{}, store.ErrNotFound
	}
	if err != nil {
		return store.CapacityRun{}, fmt.Errorf("stop capacity run: %w", err)
	}
	return s.capacityRun(ctx, got)
}

// LatestCapacityRun returns an agent's latest run with its steps, or store.ErrNotFound before its
// first.
func (s *Store) LatestCapacityRun(ctx context.Context, org, project, agent string) (store.CapacityRun, error) {
	id, err := s.q.LatestCapacityRun(ctx, LatestCapacityRunParams{OrganisationID: org, ProjectID: project, Agent: agent})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.CapacityRun{}, store.ErrNotFound
	}
	if err != nil {
		return store.CapacityRun{}, fmt.Errorf("latest capacity run: %w", err)
	}
	return s.capacityRun(ctx, id)
}

// CapacityRun returns one run with its steps, or store.ErrNotFound.
func (s *Store) CapacityRun(ctx context.Context, id string) (store.CapacityRun, error) {
	u, err := uuid(id)
	if err != nil {
		return store.CapacityRun{}, store.ErrNotFound
	}
	return s.capacityRun(ctx, u)
}

func (s *Store) capacityRun(ctx context.Context, id pgtype.UUID) (store.CapacityRun, error) {
	r, err := s.q.CapacityRunByID(ctx, id)
	if errors.Is(err, pgx.ErrNoRows) {
		return store.CapacityRun{}, store.ErrNotFound
	}
	if err != nil {
		return store.CapacityRun{}, fmt.Errorf("read capacity run: %w", err)
	}
	rows, err := s.q.CapacitySteps(ctx, id)
	if err != nil {
		return store.CapacityRun{}, fmt.Errorf("read capacity steps: %w", err)
	}
	run := store.CapacityRun{
		ID: r.ID.String(), OrganisationID: r.OrganisationID, ProjectID: r.ProjectID, Agent: r.Agent,
		MaxStep: int(r.MaxStep), RequestedAt: r.RequestedAt.Time.UTC(), State: r.State,
		StartedAt: timeOf(r.StartedAt), EndedAt: timeOf(r.EndedAt), StopRequested: r.StopRequested,
		JoinThresholdMs: intOf(r.JoinThresholdMs), Capacity: intOf(r.Capacity), LimitedBy: strOf(r.LimitedBy),
		Error: strOf(r.Error), Steps: make([]store.CapacityStep, 0, len(rows)),
	}
	if r.CloudAgent.Valid {
		v := r.CloudAgent.Bool
		run.CloudAgent = &v
	}
	for _, st := range rows {
		step := store.CapacityStep{
			Step: int(st.Step), StartedAt: st.StartedAt.Time.UTC(), EndedAt: timeOf(st.EndedAt), Outcome: st.Outcome,
			Calls: int(st.Calls), Joined: int(st.Joined), Pending: int(st.Pending), Failed: int(st.Failed),
			Late: int(st.Late), JoinSeconds: st.JoinSeconds, AnswerSeconds: st.AnswerSeconds, NoAnswer: int(st.NoAnswer),
		}
		if st.TesterCpuPercent.Valid {
			v := st.TesterCpuPercent.Float64
			step.TesterCPUPercent = &v
		}
		run.Steps = append(run.Steps, step)
	}
	return run, nil
}

func at(t time.Time) pgtype.Timestamptz { return pgtype.Timestamptz{Time: t, Valid: true} }

func i32(n int) int32 { return int32(max(min(n, math.MaxInt32), math.MinInt32)) } //nolint:gosec // clamped

func intOf(n pgtype.Int4) *int {
	if !n.Valid {
		return nil
	}
	v := int(n.Int32)
	return &v
}

func nonNil(xs []float64) []float64 {
	if xs == nil {
		return []float64{}
	}
	return xs
}
