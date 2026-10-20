package pgstore

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// PutRecordingFinding retains terminal evidence. Replays do not reopen resolved issues.
func (s *Store) PutRecordingFinding(ctx context.Context, f store.RecordingFinding) error {
	if f.EventID == "" || f.EgressID == "" {
		return errors.New("recording finding requires native event and egress IDs")
	}
	detail := "Recording lost: " + f.Status
	switch f.Status {
	case "EGRESS_FAILED", "EGRESS_ABORTED":
	case "EGRESS_LIMIT_REACHED":
		detail = "Recording cut short at the egress time limit"
	default:
		return errors.New("unsupported recording finding status")
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	callID := pgtype.UUID{}
	if f.CallID != "" {
		callID, err = uuid(f.CallID)
		if err != nil {
			return store.ErrNotFound
		}
		if _, err = q.ReadCallFindingIdentity(ctx, ReadCallFindingIdentityParams{OrganisationID: f.OrganisationID, ProjectID: f.ProjectID, ID: callID}); err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				return store.ErrNotFound
			}
			return err
		}
	}
	payload, err := json.Marshal(struct {
		Detail string `json:"detail"`
		Where  string `json:"where"`
	}{detail, f.EgressID})
	if err != nil {
		return err
	}
	if err = q.EnsureRecordingIssue(ctx, EnsureRecordingIssueParams{OrganisationID: f.OrganisationID, ProjectID: f.ProjectID}); err != nil {
		return err
	}
	n, err := q.UpsertRecordingOccurrence(ctx, UpsertRecordingOccurrenceParams{OrganisationID: f.OrganisationID, ProjectID: f.ProjectID, SourceID: "webhook:" + f.EventID, CallID: callID, OccurredAt: timestamp(f.At), Detail: payload})
	if err != nil {
		return err
	}
	if n > 0 {
		if err = q.AdvanceIssueEvidence(ctx, AdvanceIssueEvidenceParams{OrganisationID: f.OrganisationID, ProjectID: f.ProjectID, ID: "recording-lost"}); err != nil {
			return err
		}
		if err = q.AdvanceIssueRevision(ctx, AdvanceIssueRevisionParams{OrganisationID: f.OrganisationID, ProjectID: f.ProjectID}); err != nil {
			return err
		}
	}
	return tx.Commit(ctx)
}
