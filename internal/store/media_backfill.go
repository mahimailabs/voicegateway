package store

// MediaBackfill checkpoints a project's retained terminal events. Nanoseconds are exact.
type MediaBackfill struct {
	OrganisationID, ProjectID, CursorKey string
	CursorNS, Revision                   int64
}
