package store

import "time"

// RecordingFinding is a terminal egress event. It contains no provider error text or output URL.
type RecordingFinding struct {
	OrganisationID, ProjectID, EventID, EgressID, CallID, Status string
	At                                                           *time.Time
}
