package store

import (
	"errors"
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
	LimitFailed     = "failed"
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
