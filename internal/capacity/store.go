package capacity

import (
	"context"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Runs holds capacity runs: the requests web writes and what the runner measures.
type Runs interface {
	ClaimCapacityRun(ctx context.Context, runner string, now time.Time) (store.CapacityRun, bool, error)
	SetCapacityRunContext(ctx context.Context, id, runner string, joinThresholdMs int, cloudAgent *bool) error
	HeartbeatCapacityRun(ctx context.Context, id, runner string, now time.Time) (bool, error)
	PutCapacityStep(ctx context.Context, id, runner string, s store.CapacityStep) error
	EndCapacityRun(ctx context.Context, id, runner string, end store.CapacityEnd) error
	EndLostCapacityRuns(ctx context.Context, now, staleBefore, unclaimedBefore time.Time) (int64, error)
}

// Projects reads a run's project: its LiveKit URL and key, its opened secret and its join threshold.
type Projects interface {
	Project(ctx context.Context, organisationID, projectID string) (store.Project, error)
	LiveKitSecret(ctx context.Context, organisationID, projectID string) (string, error)
	ProjectSettings(ctx context.Context, organisationID, projectID string) (store.ProjectSettings, error)
}

// LiveKit is what a run does on the project's LiveKit server.
type LiveKit interface {
	// CreateRoom creates a test room that LiveKit closes 60 s after it empties.
	CreateRoom(ctx context.Context, name string) error
	DeleteRoom(ctx context.Context, name string) error
	// Dispatch asks LiveKit to send the named agent to the room.
	Dispatch(ctx context.Context, room, agent string) error
	// AgentRooms names the rooms, other than test rooms, where the agent has a job.
	AgentRooms(ctx context.Context, agent string) ([]string, error)
	// CloudAgent reports whether the agent is deployed on LiveKit Cloud; nil when it cannot tell.
	CloudAgent(ctx context.Context, agent string) *bool
	// Call joins a synthetic caller to the room. It sends what happens on events until hangup,
	// which disconnects it and waits for it to stop sending.
	Call(ctx context.Context, room string, events chan<- Event) (hangup func(), err error)
}

// Dial returns a LiveKit for one project.
type Dial func(url, apiKey, apiSecret string) LiveKit

// CPU reports how busy the tester's machine has been since the previous call, 0 to 100.
type CPU func() (float64, error)

// Event is something one synthetic call saw.
type Event struct {
	Room string
	Kind EventKind
	At   time.Time
	// Seconds is the answer time of an Answered event.
	Seconds float64
}

// EventKind says what a call saw.
type EventKind int

const (
	// Joined: the agent joined the call.
	Joined EventKind = iota + 1
	// Answered: the agent began speaking Seconds after the caller's line ended.
	Answered
	// NoAnswer: the agent did not begin speaking within AnswerWithin of the line.
	NoAnswer
	// Left: the agent left the call while it was still up.
	Left
	// Lost: the caller lost its connection to LiveKit.
	Lost
)
