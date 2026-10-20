package store

// RecordingBackfill checkpoints a project's retained terminal events. Nanoseconds are exact.
type RecordingBackfill struct {
	OrganisationID, ProjectID, CursorKey string
	CursorNS, Revision                   int64
}
