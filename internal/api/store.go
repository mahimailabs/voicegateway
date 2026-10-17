package api

import (
	"context"

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
}

// Traces reads what the agent uploaded for a call: its spans, and its turns and usage.
type Traces interface {
	CallSpans(ctx context.Context, organisationID, projectID, callID string) ([]store.Span, error)
	CallTurns(ctx context.Context, organisationID, projectID, callID string) (turn.Result, error)
}
