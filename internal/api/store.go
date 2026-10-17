package api

import (
	"context"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/turn"
)

// Projects finds the project a request names.
type Projects interface {
	Project(ctx context.Context, organisationID, projectID string) (store.Project, error)
}

// Calls reads the call index.
type Calls interface {
	Call(ctx context.Context, organisationID, projectID, callID string) (store.Call, error)
	ListCalls(ctx context.Context, f store.CallFilter, after *store.CallCursor, limit int, withTotals bool, maxValues int) ([]store.CallListRow, *store.CallTotals, time.Time, error)
	PutListSnapshot(ctx context.Context, snap store.ListSnapshot) error
	ListSnapshot(ctx context.Context, id string) (store.ListSnapshot, error)
}

// Traces reads what the agent uploaded for a call: its spans, and the turns and usage built from
// those same spans.
type Traces interface {
	CallTrace(ctx context.Context, organisationID, projectID, callID string) ([]store.Span, turn.Result, error)
}

// Layers reads what the worker stored of each project's LiveKit layers.
type Layers interface {
	LayerStatus(ctx context.Context, organisationID, projectID, layer string) (store.LayerStatus, error)
	LayerReads(ctx context.Context, organisationID, projectID, layer string, since time.Time) ([]store.LayerRead, error)
	RoomsSince(ctx context.Context, organisationID, projectID string, since time.Time, limit int) ([]store.RoomSeen, error)
	Participants(ctx context.Context, organisationID, projectID string, roomSIDs []string) ([]store.ParticipantSeen, error)
	ParticipantChanges(ctx context.Context, organisationID, projectID string, roomSIDs []string) ([]store.ParticipantChange, error)
	LatestSIP(ctx context.Context, organisationID, projectID string) (store.SIPRead, error)
}
