package store

import (
	"errors"
	"math"
	"slices"
	"time"
)

// Capacity run states (decision 0022, docs/specs/capacity-runner.md).
const (
	CapacityRequested = "requested"
	CapacityRunning   = "running"
	CapacityEnded     = "ended"
)

// What ended a capacity run. The agent's limits come first; the rest say the run could not
// measure the agent, so they never read as its capacity.
const (
	LimitPending    = "pending"
	LimitJobFailed  = "job_failed"
	LimitAnswer     = "answer"
	LimitPlan       = "plan"
	LimitMaxReached = "max_reached"
	LimitTester     = "tester"
	LimitRealCall   = "real_call"
	LimitAgentBusy  = "agent_busy"
	LimitStopped    = "stopped"
	LimitRunnerLost = "runner_lost"
	// LimitNoWorker ends a request no worker was free to claim in time; nothing was measured.
	LimitNoWorker = "no_worker"
	LimitFailed   = "failed"
)

// Step outcomes: a step is running until it ends; inconclusive means the tester, not the agent,
// was the limit.
const (
	StepRunning      = "running"
	StepPassed       = "passed"
	StepFailed       = "failed"
	StepInconclusive = "inconclusive"
)

// ErrRunActive means the project already has a capacity run that has not ended.
var ErrRunActive = errors.New("a capacity test is already running for this project")

// NewCapacityRun is what a person asks for when they start a test.
type NewCapacityRun struct {
	OrganisationID string
	ProjectID      string
	Agent          string
	MaxStep        int
	RequestedBy    string
	RequestedAt    time.Time
}

// CapacityRun is one capacity test and its steps so far. A nil field was not measured, or not yet.
type CapacityRun struct {
	ID              string
	OrganisationID  string
	ProjectID       string
	Agent           string
	MaxStep         int
	RequestedAt     time.Time
	State           string
	StartedAt       *time.Time
	EndedAt         *time.Time
	StopRequested   bool
	JoinThresholdMs *int
	CloudAgent      *bool
	Capacity        *int
	LimitedBy       *string
	Error           *string
	Steps           []CapacityStep
}

// CapacityStep is one step of a run: Calls calls up at once, and what they saw. JoinSeconds are the
// joins of the calls this step added; AnswerSeconds every answer heard during the step.
type CapacityStep struct {
	Step             int
	StartedAt        time.Time
	EndedAt          *time.Time
	Outcome          string
	Calls            int
	Joined           int
	Pending          int
	Failed           int
	Late             int
	JoinSeconds      []float64
	AnswerSeconds    []float64
	NoAnswer         int
	TesterCPUPercent *float64
}

// CapacityEnd is how a run ended.
type CapacityEnd struct {
	At        time.Time
	Capacity  *int
	LimitedBy string
	Error     string
}

// SlowAnswer is the answer time above which an answer counts as slow: one second from the end of
// the caller's line to the agent's first sound.
const SlowAnswer = 1.0

// AnswerTail is a step's answer times: the typical one and the slow end. Each is null with no
// answers, never zero. The report files, the API and the console all read it from TailOf.
type AnswerTail struct {
	Count     int      `json:"count"`
	Median    *float64 `json:"medianSeconds"`
	P95       *float64 `json:"p95Seconds"`
	SlowShare *float64 `json:"overOneSecondShare"`
	Slowest   *float64 `json:"slowestSeconds"`
}

// TailOf summarises answer times. P95 is the nearest-rank 95th percentile.
func TailOf(answers []float64) AnswerTail {
	t := AnswerTail{Count: len(answers)}
	if len(answers) == 0 {
		return t
	}
	s := slices.Clone(answers)
	slices.Sort(s)
	med := s[len(s)/2]
	if len(s)%2 == 0 {
		med = (s[len(s)/2-1] + s[len(s)/2]) / 2
	}
	p95 := s[int(math.Ceil(0.95*float64(len(s))))-1]
	slow := 0
	for _, a := range s {
		if a > SlowAnswer {
			slow++
		}
	}
	share := float64(slow) / float64(len(s))
	last := s[len(s)-1]
	t.Median, t.P95, t.SlowShare, t.Slowest = &med, &p95, &share, &last
	return t
}
