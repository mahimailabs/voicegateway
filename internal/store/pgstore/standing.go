package pgstore

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"slices"
	"strings"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// ApplyStandingFindings commits source health and findings together. Replayed/out-of-order reads
// cannot overwrite a newer observation. A failed read records health without clearing evidence.
func (s *Store) ApplyStandingFindings(ctx context.Context, read store.LayerRead, evaluation *store.StandingEvaluation) error {
	source := ""
	switch read.Layer {
	case store.LayerSIP:
		source = "phone_bridge"
	case store.LayerCloudAgents, store.LayerWorkers, store.LayerMediaServer:
		source = "agent_workers"
	case store.LayerEgress, store.LayerIngress:
		source = "delivery"
	default:
		return errors.New("unsupported issue source layer")
	}
	if read.ReadAt.IsZero() || read.Every <= 0 || read.Every > 24*time.Hour {
		return errors.New("issue source requires a timestamp and bounded read interval")
	}
	if read.OK && evaluation == nil {
		return errors.New("successful source requires explicit evaluation")
	}
	if !read.OK && !slices.Contains([]string{store.ErrorUnauthenticated, store.ErrorPermissionDenied, store.ErrorUnreachable, store.ErrorNotSupported, store.ErrorOther}, read.ErrorClass) {
		return errors.New("failed source requires a known error class")
	}
	if read.OK && read.ErrorClass != "" {
		return errors.New("successful source cannot have an error")
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	freshUntil := read.ReadAt.Add(3 * read.Every)
	n, err := q.ClaimIssueSource(ctx, ClaimIssueSourceParams{OrganisationID: read.OrganisationID, ProjectID: read.ProjectID, Layer: read.Layer, ReadAt: timestamp(&read.ReadAt), Ok: read.OK, FreshUntil: timestamp(&freshUntil), ErrorClass: read.ErrorClass})
	if err != nil {
		return err
	}
	if n == 0 {
		return nil
	}
	if !read.OK {
		if err := q.AdvanceIssueRevision(ctx, AdvanceIssueRevisionParams{OrganisationID: read.OrganisationID, ProjectID: read.ProjectID}); err != nil {
			return err
		}
		return tx.Commit(ctx)
	}
	ordered := slices.Clone(evaluation.Findings)
	slices.SortFunc(ordered, func(a, b store.StandingFinding) int {
		if c := cmp.Compare(a.ID, b.ID); c != 0 {
			return c
		}
		return cmp.Compare(a.SourceID, b.SourceID)
	})
	present := []store.FindingKey{}
	for _, f := range ordered {
		key := store.FindingKey{IssueID: f.ID, SourceID: f.SourceID}
		if !strings.HasPrefix(f.ID, read.Layer+":") || f.SourceID == "" || !slices.Contains(evaluation.Subjects, f.SourceID) || slices.Contains(present, key) {
			return errors.New("standing findings require unique source-scoped identities")
		}
		present = append(present, key)
		detail, err := json.Marshal(struct {
			Detail string `json:"detail"`
			Where  string `json:"where"`
		}{f.Detail, f.Where})
		if err != nil {
			return err
		}
		if err := q.EnsureStandingIssue(ctx, EnsureStandingIssueParams{OrganisationID: read.OrganisationID, ProjectID: read.ProjectID, ID: f.ID, Title: f.Title, Source: source, Severity: f.Severity}); err != nil {
			return err
		}
		changed, err := q.UpsertStandingOccurrence(ctx, UpsertStandingOccurrenceParams{OrganisationID: read.OrganisationID, ProjectID: read.ProjectID, IssueID: f.ID, SourceID: f.SourceID, SourceLayer: read.Layer, Detail: detail})
		if err != nil {
			return err
		}
		if changed > 0 {
			if err := q.AdvanceIssueEvidence(ctx, AdvanceIssueEvidenceParams{OrganisationID: read.OrganisationID, ProjectID: read.ProjectID, ID: f.ID}); err != nil {
				return err
			}
		}
	}
	covered := evaluation.Covered
	if covered == nil {
		covered = []store.FindingKey{}
	}
	for _, key := range covered {
		if !strings.HasPrefix(key.IssueID, read.Layer+":") || !slices.Contains(evaluation.Subjects, key.SourceID) {
			return errors.New("invalid standing rule coverage")
		}
	}
	coverageJSON, err := json.Marshal(covered)
	if err != nil {
		return err
	}
	presentJSON, err := json.Marshal(present)
	if err != nil {
		return err
	}
	if _, err := q.DeactivateStandingFindings(ctx, DeactivateStandingFindingsParams{OrganisationID: read.OrganisationID, ProjectID: read.ProjectID, SourceLayer: read.Layer, Subjects: nonNilStrings(evaluation.Subjects), Covered: coverageJSON, Present: presentJSON}); err != nil {
		return err
	}
	if err := q.ConfirmStandingFindings(ctx, ConfirmStandingFindingsParams{OrganisationID: read.OrganisationID, ProjectID: read.ProjectID, SourceLayer: read.Layer, ReadAt: timestamp(&read.ReadAt), Present: presentJSON}); err != nil {
		return err
	}
	if err := q.AdvanceIssueRevision(ctx, AdvanceIssueRevisionParams{OrganisationID: read.OrganisationID, ProjectID: read.ProjectID}); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
