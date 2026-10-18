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
	EgressSince(ctx context.Context, organisationID, projectID string, since time.Time, limit int) ([]store.EgressSeen, error)
	EgressChanges(ctx context.Context, organisationID, projectID string, ids []string) ([]store.StatusChange, error)
	EgressWebhooks(ctx context.Context, organisationID, projectID string, ids []string, since time.Time) ([]store.WebhookStatus, error)
	IngressSince(ctx context.Context, organisationID, projectID string, since time.Time, limit int) ([]store.IngressSeen, error)
	IngressChanges(ctx context.Context, organisationID, projectID string, ids []string) ([]store.StatusChange, error)
	IngressWebhooks(ctx context.Context, organisationID, projectID string, ids []string, since time.Time) ([]store.WebhookStatus, error)
	LatestCloudAgents(ctx context.Context, organisationID, projectID string) (store.CloudAgentsRead, error)
	WorkerSamples(ctx context.Context, organisationID, projectID string, since time.Time, limit int) ([]store.WorkerSample, error)
	WorkerEndpointStatus(ctx context.Context, organisationID, projectID string) (map[string]store.LayerStatus, error)
}

// WorkerEndpoints lists the worker metrics endpoints a project lists, by id and host; their URLs
// are never read here.
type WorkerEndpoints interface {
	WorkerEndpoints(ctx context.Context, organisationID, projectID string) ([]store.WorkerEndpoint, error)
}

// Accounts reads what a signed-in user may see, and changes an organisation's members.
type Accounts interface {
	Workspaces(ctx context.Context, userID string) ([]store.Workspace, error)
	Members(ctx context.Context, organisationID string) ([]store.Member, error)
	UpdateMember(ctx context.Context, organisationID, userID, role string) (store.Member, error)
	RemoveMember(ctx context.Context, organisationID, userID string) error
}
