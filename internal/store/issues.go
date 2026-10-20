package store

import (
	"errors"
	"time"
)

var (
	ErrInvalidIssueState = errors.New("issue state must be open, resolved or ignored")
	ErrIssueConflict     = errors.New("issue changed since it was read")
)

// IssueMetadata keeps user decisions independent from the evidence and archive state. A resolved
// issue regresses when a new occurrence is observed, even if its source timestamp predates the
// decision. Replaying the same occurrence must not change EvidenceRevision.
type IssueMetadata struct {
	ID               string     `json:"issueId"`
	State            string     `json:"state"`
	Regressed        bool       `json:"regressed"`
	DecidedAt        *time.Time `json:"decidedAt"`
	EvidenceRevision int64      `json:"evidenceRevision"`
	ReviewedRevision int64      `json:"reviewedEvidenceRevision"`
	Revision         int64      `json:"revision"`
	Tags             []string   `json:"tags"`
	Archived         bool       `json:"archived"`
}
