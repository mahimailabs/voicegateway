package store

import (
	"errors"
	"time"
)

// Project is one LiveKit project inside an organisation (decision 0016). The LiveKit API secret is
// not part of it: only ingest opens it, through the store that sealed it.
type Project struct {
	OrganisationID string
	ID             string
	Name           string
	LiveKitURL     string
	LiveKitAPIKey  string
	CreatedAt      time.Time
}

// ErrLiveKitKeyInUse means another project already uses the LiveKit API key, which ingest relies on
// to find the project an event belongs to.
var ErrLiveKitKeyInUse = errors.New("LiveKit API key is already used by another project")

// ErrProjectExists means the organisation already has a project with that id.
var ErrProjectExists = errors.New("a project with this id already exists")

// ErrBadRow means storage refused a row for what it holds, not because it is unreachable or busy:
// retrying the same row cannot succeed.
var ErrBadRow = errors.New("row refused by storage")

// ErrNotFound means no record matched.
var ErrNotFound = errors.New("not found")

// Event is one ingested envelope as the worker stores it, before calls are joined.
type Event struct {
	OrganisationID string
	ProjectID      string
	Kind           string
	// DedupeKey is the source's event id when it sent one, else the stream entry id.
	DedupeKey string
	// EventID is the source's own id, empty when unknown.
	EventID    string
	StreamID   string
	ReceivedAt time.Time
	Payload    []byte
}

// EventKey is what makes an Event unique.
type EventKey struct {
	OrganisationID string
	ProjectID      string
	Kind           string
	DedupeKey      string
}

// Key returns the event's dedupe key.
func (e Event) Key() EventKey {
	return EventKey{OrganisationID: e.OrganisationID, ProjectID: e.ProjectID, Kind: e.Kind, DedupeKey: e.DedupeKey}
}
