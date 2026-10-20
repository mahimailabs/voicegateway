package pgstore

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"slices"

	"github.com/jackc/pgx/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// ApplyCallFindings replaces the observed findings for one immutable call. nil means the source
// was not evaluated, and preserves prior evidence; an empty slice is an evaluated empty result.
func (s *Store) ApplyCallFindings(ctx context.Context, org, project, callID string, findings []store.Finding) error {
	if findings == nil {
		return nil
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if err := applyCallFindings(ctx, s.q.WithTx(tx), org, project, callID, findings, nil); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func applyCallFindings(ctx context.Context, q *Queries, org, project, callID string, findings []store.Finding, evaluated []string) error {
	if findings == nil {
		return nil
	}
	id, err := uuid(callID)
	if err != nil {
		return store.ErrNotFound
	}
	call, err := q.ReadCallFindingIdentity(ctx, ReadCallFindingIdentityParams{OrganisationID: org, ProjectID: project, ID: id})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.ErrNotFound
	}
	if err != nil {
		return err
	}
	// Stable lock order prevents two calls with the same issue groups taking opposing locks.
	ordered := slices.Clone(findings)
	slices.SortFunc(ordered, func(a, b store.Finding) int {
		if a.ID < b.ID {
			return -1
		}
		if a.ID > b.ID {
			return 1
		}
		return 0
	})
	ids := make([]string, 0, len(ordered))
	changed := false
	for _, finding := range ordered {
		if finding.ID == "" || (len(ids) > 0 && ids[len(ids)-1] == finding.ID) {
			return errors.New("findings require unique nonempty rule IDs")
		}
		ids = append(ids, finding.ID)
		if finding.Turns == nil {
			finding.Turns = []int{}
		}
		detail, err := json.Marshal(struct {
			store.Finding
			Where string `json:"where"`
		}{finding, call.RoomName.String})
		if err != nil {
			return err
		}
		if err := q.EnsureCallIssue(ctx, EnsureCallIssueParams{OrganisationID: org, ProjectID: project, ID: finding.ID, Title: finding.Title, Severity: finding.Severity}); err != nil {
			return fmt.Errorf("ensure call issue: %w", err)
		}
		n, err := q.UpsertIssueOccurrence(ctx, UpsertIssueOccurrenceParams{
			OrganisationID: org, ProjectID: project, IssueID: finding.ID, SourceID: callID,
			CallID: id, OccurredAt: call.OccurredAt, Detail: detail,
		})
		if err != nil {
			return err
		}
		if n > 0 {
			if err := q.AdvanceIssueEvidence(ctx, AdvanceIssueEvidenceParams{OrganisationID: org, ProjectID: project, ID: finding.ID}); err != nil {
				return err
			}
			changed = true
		}
	}
	n, err := q.DeactivateCallFindings(ctx, DeactivateCallFindingsParams{OrganisationID: org, ProjectID: project, CallID: id, IssueIds: ids, EvaluatedIds: evaluated})
	if err != nil {
		return err
	}
	if changed || n > 0 {
		return q.AdvanceIssueRevision(ctx, AdvanceIssueRevisionParams{OrganisationID: org, ProjectID: project})
	}
	return nil
}

func (s *Store) IssueMetadata(ctx context.Context, org, project, issueID string) (store.IssueMetadata, error) {
	row, err := s.q.ReadIssueMetadata(ctx, ReadIssueMetadataParams{OrganisationID: org, ProjectID: project, ID: issueID})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.IssueMetadata{}, store.ErrNotFound
	}
	if err != nil {
		return store.IssueMetadata{}, err
	}
	return issueMetadata(row), nil
}

// UpdateIssueMetadata compares both the editor's metadata and evidence revisions. A nil state
// changes only tags/archive; it must not acknowledge new evidence or clear a regression.
func (s *Store) UpdateIssueMetadata(ctx context.Context, org, project, issueID string, revision, evidence int64, state *string, tags []string, archived bool) (store.IssueMetadata, error) {
	if revision < 0 || revision >= 9007199254740991 || evidence < 0 || evidence > 9007199254740991 {
		return store.IssueMetadata{}, store.ErrInvalidMetadata
	}
	if state != nil && !slices.Contains([]string{"open", "resolved", "ignored"}, *state) {
		return store.IssueMetadata{}, store.ErrInvalidIssueState
	}
	normalized, err := store.NormalizeTags(tags)
	if err != nil {
		return store.IssueMetadata{}, err
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return store.IssueMetadata{}, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	row, err := q.UpdateIssueMetadata(ctx, UpdateIssueMetadataParams{
		OrganisationID: org, ProjectID: project, ID: issueID, ExpectedRevision: revision,
		SeenEvidenceRevision: evidence, NewState: text(deref(state)), Tags: normalized, Archived: archived,
	})
	if errors.Is(err, pgx.ErrNoRows) {
		if _, readErr := q.ReadIssueMetadata(ctx, ReadIssueMetadataParams{OrganisationID: org, ProjectID: project, ID: issueID}); errors.Is(readErr, pgx.ErrNoRows) {
			return store.IssueMetadata{}, store.ErrNotFound
		} else if readErr != nil {
			return store.IssueMetadata{}, readErr
		}
		return store.IssueMetadata{}, store.ErrIssueConflict
	}
	if err != nil {
		return store.IssueMetadata{}, err
	}
	if err := q.AdvanceIssueRevision(ctx, AdvanceIssueRevisionParams{OrganisationID: org, ProjectID: project}); err != nil {
		return store.IssueMetadata{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return store.IssueMetadata{}, err
	}
	return issueMetadata(ReadIssueMetadataRow(row)), nil
}

func issueMetadata(row ReadIssueMetadataRow) store.IssueMetadata {
	regressed := row.State == "resolved" && row.EvidenceRevision > row.ReviewedEvidenceRevision
	state := row.State
	if regressed {
		state = "open"
	}
	return store.IssueMetadata{
		ID: row.ID, State: state, Regressed: regressed,
		DecidedAt: timeOf(row.DecidedAt), EvidenceRevision: row.EvidenceRevision,
		ReviewedRevision: row.ReviewedEvidenceRevision, Revision: row.MetadataRevision,
		Tags: nonNilStrings(row.Tags), Archived: row.Archived,
	}
}
