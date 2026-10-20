package store

import "time"

// ResourceEndpoint binds an exact container to a project-owned component. Host is
// safe for display; the credential-bearing exporter URL is sealed separately.
type ResourceEndpoint struct {
	OrganisationID string
	ProjectID      string
	ID             string
	Host           string
	Kind           string
	Target         string
	ContainerID    string
	CreatedAt      time.Time
}

type OpenResourceEndpoint struct {
	ResourceEndpoint
	URL string
}

// ResourceSample describes one container, including its child processes. CPUUsed
// is cores consumed, not host percent. Missing allocation is not zero capacity.
type ResourceSample struct {
	OrganisationID   string
	ProjectID        string
	EndpointID       string
	ReadAt           time.Time
	ObservedAt       time.Time
	StartedAt        *float64
	CPUSeconds       *float64
	CPUUsed          *float64
	CPUCores         *float64
	MemoryBytes      *float64
	MemoryLimitBytes *float64
}
