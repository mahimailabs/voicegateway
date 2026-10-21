package pgstore

import (
	"context"
	"encoding/json"
	"errors"
	"slices"

	"github.com/jackc/pgx/v5/pgtype"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func (s *Store) PendingJobFindings(ctx context.Context, limit int) ([]store.PendingJobFinding, error) {
	if limit < 1 || limit > 1000 {
		return nil, errors.New("invalid job findings batch limit")
	}
	rows, err := s.q.PendingJobFindings(ctx, int32(limit)) //nolint:gosec // bounded above
	if err != nil {
		return nil, err
	}
	out := make([]store.PendingJobFinding, 0, len(rows))
	for _, r := range rows {
		item := store.PendingJobFinding{OrganisationID: r.OrganisationID, ProjectID: r.ProjectID, JobID: r.JobID, RoomSID: r.RoomSid, DispatchID: r.DispatchID, Status: r.Status, DispatchedAt: timeOf(r.DispatchedAt), JoinedAt: timeOf(r.JoinedAt), ReportedFailedAt: timeOf(r.ReportedFailedAt), FailureObservedAt: timeOf(r.FailureObservedAt), ObservationRevision: r.ObservationRevision, EvaluationRevision: r.EvaluationRevision}
		if r.AgentName.Valid {
			item.AgentName = &r.AgentName.String
		}
		if r.CallID.Valid {
			id := r.CallID.String()
			item.CallID = &id
		}
		out = append(out, item)
	}
	return out, nil
}

func (s *Store) PutJobFindings(ctx context.Context, job store.PendingJobFinding, findings []store.JobFinding, covered []string, settingsRevision int64) error {
	callID := pgtype.UUID{}
	if job.CallID != nil {
		var err error
		callID, err = uuid(*job.CallID)
		if err != nil {
			return err
		}
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	n, err := q.ClaimJobFindings(ctx, ClaimJobFindingsParams{OrganisationID: job.OrganisationID, ProjectID: job.ProjectID, JobID: job.JobID, ObservationRevision: job.ObservationRevision, ExpectedRevision: job.EvaluationRevision, SettingsRevision: settingsRevision, CallID: callID, JoinedAt: timestamp(job.JoinedAt)})
	if err != nil || n == 0 {
		return err
	}
	ordered := slices.Clone(findings)
	slices.SortFunc(ordered, func(a, b store.JobFinding) int {
		if a.ID < b.ID {
			return -1
		}
		if a.ID > b.ID {
			return 1
		}
		return 0
	})
	present := []string{}
	sourceID := "job:" + job.JobID
	changed := false
	for _, f := range ordered {
		if (f.ID != "job-failed" && f.ID != "slow-join") || slices.Contains(present, f.ID) {
			return errors.New("unsupported or duplicate job finding")
		}
		present = append(present, f.ID)
		detail, err := json.Marshal(struct {
			Detail string `json:"detail"`
			Where  string `json:"where"`
		}{f.Detail, job.JobID})
		if err != nil {
			return err
		}
		if err = q.EnsureJobIssue(ctx, EnsureJobIssueParams{OrganisationID: job.OrganisationID, ProjectID: job.ProjectID, ID: f.ID, Title: f.Title, Severity: f.Severity}); err != nil {
			return err
		}
		up, err := q.UpsertJobOccurrence(ctx, UpsertJobOccurrenceParams{OrganisationID: job.OrganisationID, ProjectID: job.ProjectID, IssueID: f.ID, SourceID: sourceID, CallID: callID, OccurredAt: timestamp(f.At), Detail: detail})
		if err != nil {
			return err
		}
		changed = changed || up.Changed
		if up.Fresh.Bool {
			if err = q.AdvanceIssueEvidence(ctx, AdvanceIssueEvidenceParams{OrganisationID: job.OrganisationID, ProjectID: job.ProjectID, ID: f.ID}); err != nil {
				return err
			}
		}
	}
	for _, id := range covered {
		if id != "job-failed" && id != "slow-join" {
			return errors.New("unsupported job finding coverage")
		}
	}
	n, err = q.DeactivateJobFindings(ctx, DeactivateJobFindingsParams{OrganisationID: job.OrganisationID, ProjectID: job.ProjectID, SourceID: sourceID, Covered: nonNilStrings(covered), Present: present})
	if err != nil {
		return err
	}
	if changed || n > 0 {
		if err = q.AdvanceIssueRevision(ctx, AdvanceIssueRevisionParams{OrganisationID: job.OrganisationID, ProjectID: job.ProjectID}); err != nil {
			return err
		}
	}
	return tx.Commit(ctx)
}
