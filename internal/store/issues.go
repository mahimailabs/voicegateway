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

// IssueList is read from a single snapshot. It returns every matching occurrence, so table
// filtering and totals do not depend on a partially loaded call list.
type IssueList struct {
	From     time.Time `json:"from"`
	To       time.Time `json:"to"`
	Complete bool      `json:"complete"`
	Items    []Issue   `json:"items"`
}
type Issue struct {
	ID         string        `json:"id"`
	Title      string        `json:"title"`
	Kind       string        `json:"kind"`
	Source     string        `json:"source"`
	Severity   string        `json:"severity"`
	LastSeenAt *time.Time    `json:"lastSeenAt"`
	Hits       []IssueHit    `json:"hits"`
	Metadata   IssueMetadata `json:"metadata"`
}
type IssueHit struct {
	ObservedAt    *time.Time `json:"observedAt"`
	EvidenceState string     `json:"evidenceState"`
	At            *time.Time `json:"at"`
	Where         string     `json:"where"`
	Detail        string     `json:"detail"`
	CallID        *string    `json:"callId"`
}
type IssueFilter struct {
	From, To time.Time
	Search   string
	Tags     []string
	Archived *bool
}

// PendingFindings identifies a historical call and the evaluation version observed by the worker.
// A newer evaluation wins over a stale backfill attempt.
type PendingFindings struct {
	OrganisationID, ProjectID, ID string
	Revision                      int64
}

// FindingKey identifies one rule on one component. Coverage is explicit: an unknown field cannot
// clear its previous finding merely because the surrounding component read succeeded.
type FindingKey struct {
	IssueID  string `json:"issueId"`
	SourceID string `json:"sourceId"`
}
type StandingFinding struct {
	Finding
	SourceID string
	Where    string
}
type StandingEvaluation struct {
	Findings []StandingFinding
	Covered  []FindingKey
	// Subjects contains every component in a successful complete source snapshot. An absent
	// subject has been deleted; a present subject with unknown fields preserves those findings.
	Subjects []string
}

// PendingJobFinding contains exact native job and participant evidence. It is worker-only data.
type PendingJobFinding struct {
	OrganisationID, ProjectID, JobID, RoomSID, DispatchID, Status string
	AgentName, CallID                                             *string
	DispatchedAt, JoinedAt, ReportedFailedAt, FailureObservedAt   *time.Time
	ObservationRevision, EvaluationRevision                       int64
}

// JobFinding carries the timestamp evidence of one native job event.
type JobFinding struct {
	Finding
	At *time.Time
}
