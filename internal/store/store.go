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

// ErrNotFound means no record matched.
var ErrNotFound = errors.New("not found")
