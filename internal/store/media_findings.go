package store

import "time"

// MediaFinding is a terminal media failure event. It contains no provider error text or output URL.
type MediaFinding struct {
	OrganisationID, ProjectID, EventID, ComponentID, CallID, Status, Layer string
	At                                                                     *time.Time
}
