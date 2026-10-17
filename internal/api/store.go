package api

import (
	"context"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Projects finds the project a request names.
type Projects interface {
	Project(ctx context.Context, organisationID, projectID string) (store.Project, error)
}

// Layers reads what the worker stored of each project's LiveKit layers.
type Layers interface {
	LayerStatus(ctx context.Context, organisationID, projectID, layer string) (store.LayerStatus, error)
	LayerReads(ctx context.Context, organisationID, projectID, layer string, since time.Time) ([]store.LayerRead, error)
	RoomsSince(ctx context.Context, organisationID, projectID string, since time.Time, limit int) ([]store.RoomSeen, error)
	Participants(ctx context.Context, organisationID, projectID string, roomSIDs []string) ([]store.ParticipantSeen, error)
	ParticipantChanges(ctx context.Context, organisationID, projectID string, roomSIDs []string) ([]store.ParticipantChange, error)
}
