package pgstore

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"time"

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

func (s *Store) ListIssues(ctx context.Context, org, project string, filter store.IssueFilter) (store.IssueList, error) {
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return store.IssueList{}, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	if _, err := q.ProjectConnected(ctx, ProjectConnectedParams{OrganisationID: org, ProjectID: project}); errors.Is(err, pgx.ErrNoRows) {
		return store.IssueList{}, store.ErrNotFound
	} else if err != nil {
		return store.IssueList{}, err
	}
	rows, err := q.ListIssueOccurrences(ctx, ListIssueOccurrencesParams{OrganisationID: org, ProjectID: project, FromAt: timestamp(&filter.From), ToAt: timestamp(&filter.To), Archived: optionalBool(filter.Archived), Tags: nonNilStrings(filter.Tags), Search: filter.Search})
	if err != nil {
		return store.IssueList{}, err
	}

	evaluation, err := q.IssueEvaluationCoverage(ctx, IssueEvaluationCoverageParams{OrganisationID: org, ProjectID: project, FromAt: timestamp(&filter.From), ToAt: timestamp(&filter.To)})
	if err != nil {
		return store.IssueList{}, err
	}
	sources, err := q.IssueSourceCoverage(ctx, IssueSourceCoverageParams{OrganisationID: org, ProjectID: project})
	if err != nil {
		return store.IssueList{}, err
	}
	coverage := store.IssueCoverage{PendingCalls: evaluation.PendingCalls, UnavailableCalls: evaluation.UnavailableCalls, PendingJobs: evaluation.PendingJobs, Sources: []store.IssueSourceCoverage{}}
	complete := evaluation.PendingCalls == 0 && evaluation.UnavailableCalls == 0 && evaluation.PendingJobs == 0
	for _, layer := range []string{store.LayerMediaServer, store.LayerSIP, store.LayerCloudAgents, store.LayerEgress, store.LayerIngress} {
		source := store.IssueSourceCoverage{Layer: layer, State: "unknown"}
		for _, row := range sources {
			if row.Layer == layer {
				source.State = row.State
				source.ReadAt = timeOf(row.ReadAt)
				break
			}
		}
		coverage.Sources = append(coverage.Sources, source)
		complete = complete && source.State == "current"
	}
	out := store.IssueList{From: filter.From, To: filter.To, Complete: complete, Coverage: coverage, Items: []store.Issue{}}

	positions := map[string]int{}
	for _, row := range rows {
		pos, ok := positions[row.ID]
		if !ok {
			pos = len(out.Items)
			positions[row.ID] = pos
			metadata := issueMetadata(ReadIssueMetadataRow{ID: row.ID, State: row.State, DecidedAt: row.DecidedAt, EvidenceRevision: row.EvidenceRevision, ReviewedEvidenceRevision: row.ReviewedEvidenceRevision, MetadataRevision: row.MetadataRevision, Tags: row.Tags, Archived: row.Archived})
			out.Items = append(out.Items, store.Issue{ID: row.ID, Title: row.Title, Kind: row.Kind, Source: row.Source, Severity: row.Severity, LastSeenAt: timeOf(row.OccurredAt), Hits: []store.IssueHit{}, Metadata: metadata})
		}
		var detail struct {
			Where  string `json:"where"`
			Detail string `json:"detail"`
		}
		if err := json.Unmarshal(row.Detail, &detail); err != nil {
			return store.IssueList{}, fmt.Errorf("decode issue occurrence: %w", err)
		}
		hit := store.IssueHit{ObservedAt: timeOf(row.ObservedAt), EvidenceState: row.EvidenceState, At: timeOf(row.OccurredAt), Where: detail.Where, Detail: detail.Detail}
		if row.CallID.Valid {
			id := row.CallID.String()
			hit.CallID = &id
		}
		out.Items[pos].Hits = append(out.Items[pos].Hits, hit)
	}
	if err := tx.Commit(ctx); err != nil {
		return store.IssueList{}, err
	}
	return out, nil
}

func (s *Store) PendingCallFindings(ctx context.Context, limit int) ([]store.PendingFindings, error) {
	rows, err := s.q.PendingCallFindings(ctx, int32(min(max(limit, 1), 1000)))
	if err != nil {
		return nil, err
	}
	out := make([]store.PendingFindings, 0, len(rows))
	for _, row := range rows {
		out = append(out, store.PendingFindings{OrganisationID: row.OrganisationID, ProjectID: row.ProjectID, ID: row.ID.String(), Revision: row.IssueEvaluationRevision})
	}
	return out, nil
}

func (s *Store) PutCallFindings(ctx context.Context, call store.PendingFindings, findings []store.Finding, covered []string, settingsRevision int64, available bool, at time.Time) error {
	id, err := uuid(call.ID)
	if err != nil {
		return store.ErrNotFound
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	n, err := q.ClaimBackfillFindings(ctx, ClaimBackfillFindingsParams{ID: id, OrganisationID: call.OrganisationID, ProjectID: call.ProjectID, EvaluatedAt: timestamp(&at), ExpectedRevision: call.Revision, SettingsRevision: settingsRevision, SourceAvailable: available})
	if err != nil {
		return err
	}
	if n == 0 {
		return nil
	}
	if err := applyCallFindings(ctx, q, call.OrganisationID, call.ProjectID, call.ID, findings, covered); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Store) DeferCallFindings(ctx context.Context, call store.PendingFindings) error {
	uid, err := uuid(call.ID)
	if err != nil {
		return store.ErrNotFound
	}
	return s.q.DeferCallFindings(ctx, DeferCallFindingsParams{ID: uid, ExpectedRevision: call.Revision})
}
