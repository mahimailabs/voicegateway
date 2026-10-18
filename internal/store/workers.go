package store

import (
	"errors"
	"time"
)

// WorkerEndpoint is one self-hosted agent worker's Prometheus metrics endpoint listed for a project
// (phase 05d). Host is the endpoint's scheme and host only: the URL itself is sealed at rest, since
// its user part, path or query can carry a credential, and is opened only by the worker that reads
// it.
type WorkerEndpoint struct {
	OrganisationID string
	ProjectID      string
	ID             string
	Host           string
	CreatedAt      time.Time
}

// OpenWorkerEndpoint is a listed endpoint with its URL opened, for the worker's read only.
type OpenWorkerEndpoint struct {
	WorkerEndpoint
	URL string
}

// ErrEndpointExists means the project already lists that worker metrics URL.
var ErrEndpointExists = errors.New("the project already lists this worker metrics URL")

// WorkerEndpointRead is one attempt to read one listed endpoint. A failed read carries its error
// class and no samples.
type WorkerEndpointRead struct {
	EndpointID string
	ReadAt     time.Time
	OK         bool
	// ErrorClass is empty when OK.
	ErrorClass string
	Every      time.Duration
}

// WorkerSample is what one read of an endpoint reported for one worker, by its nodename label.
// Load and ActiveJobs are nil when the endpoint did not export that metric for the worker.
type WorkerSample struct {
	OrganisationID string
	ProjectID      string
	ReadAt         time.Time
	EndpointID     string
	Nodename       string
	Load           *float64
	ActiveJobs     *int64
}
