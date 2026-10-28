package pgstore

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"slices"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// alertLockKey is the advisory lock that keeps alert evaluation to one worker at a time.
const alertLockKey = 7_302_011

// TryAlertLock takes the evaluation lock on its own connection, or reports that another worker
// holds it. release gives it back and returns the connection.
func (s *Store) TryAlertLock(ctx context.Context) (release func(), ok bool, err error) {
	conn, err := s.pool.Acquire(ctx)
	if err != nil {
		return nil, false, fmt.Errorf("alert lock: %w", err)
	}
	if err := conn.QueryRow(ctx, "SELECT pg_try_advisory_lock($1)", alertLockKey).Scan(&ok); err != nil {
		conn.Release()
		return nil, false, fmt.Errorf("alert lock: %w", err)
	}
	if !ok {
		conn.Release()
		return nil, false, nil
	}
	return func() {
		// A failed unlock closes the connection, which drops the session lock with it.
		if _, err := conn.Exec(context.WithoutCancel(ctx), "SELECT pg_advisory_unlock($1)", alertLockKey); err != nil {
			_ = conn.Conn().Close(context.WithoutCancel(ctx))
		}
		conn.Release()
	}, true, nil
}

func ts(t time.Time) pgtype.Timestamptz { return pgtype.Timestamptz{Time: t, Valid: true} }

func capRows(n int) int32 { return int32(min(max(n, 0), math.MaxInt32)) } //nolint:gosec // bounded

func alertIncident(r AlertIncident) (store.AlertIncident, error) {
	inc := store.AlertIncident{
		ID: r.ID.String(), OrganisationID: r.OrganisationID, ProjectID: r.ProjectID, RuleID: r.RuleID,
		Subject: r.Subject, SubjectLabel: r.SubjectLabel, State: r.State, OpenedAt: r.OpenedAt.Time.UTC(),
		ResolvedAt: timeOf(r.ResolvedAt), ResolvedBy: strOf(r.ResolvedBy), Occurrences: int(r.Occurrences),
		NotifiedOccurrences: int(r.NotifiedOccurrences), NotifiedAt: timeOf(r.NotifiedAt), Changes: int(r.Changes),
		LastOccurrenceAt: timeOf(r.LastOccurrenceAt), HoldReason: strOf(r.HoldReason), SilentSince: timeOf(r.SilentSince),
	}
	if err := json.Unmarshal(r.LastDetail, &inc.Last); err != nil {
		return inc, fmt.Errorf("alert incident %s detail: %w", inc.ID, err)
	}
	return inc, nil
}

// NewAlertOccurrences returns occurrences stored since a time that no incident counted yet,
// oldest first.
func (s *Store) NewAlertOccurrences(ctx context.Context, since time.Time, limit int) ([]store.AlertOccurrence, error) {
	rows, err := s.q.NewAlertOccurrences(ctx, NewAlertOccurrencesParams{Since: ts(since), MaxRows: capRows(limit)})
	if err != nil {
		return nil, fmt.Errorf("new alert occurrences: %w", err)
	}
	out := make([]store.AlertOccurrence, 0, len(rows))
	for _, r := range rows {
		out = append(out, store.AlertOccurrence{
			OrganisationID: r.OrganisationID, ProjectID: r.ProjectID, RuleID: r.RuleID, OccurrenceID: r.OccurrenceID,
			Subject: r.Subject, At: timeOf(r.OccurredAt),
			Detail: store.AlertDetail{Where: r.Location, Detail: r.Detail, CallID: uuidStr(r.CallID)},
		})
	}
	return out, nil
}

// StandingAlerts lists every standing condition true now.
func (s *Store) StandingAlerts(ctx context.Context) ([]store.StandingAlert, error) {
	rows, err := s.q.StandingAlertOccurrences(ctx)
	if err != nil {
		return nil, fmt.Errorf("standing alerts: %w", err)
	}
	out := make([]store.StandingAlert, 0, len(rows))
	for _, r := range rows {
		out = append(out, store.StandingAlert{
			OrganisationID: r.OrganisationID, ProjectID: r.ProjectID, SourceID: r.SourceID,
			ChangedAt: r.ChangedAt.Time.UTC(), Where: r.Location, Detail: r.Detail,
		})
	}
	return out, nil
}

// ApplyAlertOccurrence counts one occurrence, once per rule, in one transaction: it joins the
// subject's firing incident, reopens one resolved since flapSince (no message), or opens a new one
// and queues its firing deliveries. outcome is duplicate, joined, reopened or opened.
func (s *Store) ApplyAlertOccurrence(ctx context.Context, occ store.AlertOccurrence, label string, now, flapSince time.Time, compose store.AlertCompose) (store.AlertIncident, string, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return store.AlertIncident{}, "", err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	n, err := q.InsertAlertOccurrence(ctx, InsertAlertOccurrenceParams{OrganisationID: occ.OrganisationID, ProjectID: occ.ProjectID, RuleID: occ.RuleID, OccurrenceID: occ.OccurrenceID})
	if err != nil {
		return store.AlertIncident{}, "", fmt.Errorf("alert occurrence: %w", err)
	}
	if n == 0 {
		return store.AlertIncident{}, "duplicate", nil
	}
	detail, err := json.Marshal(occ.Detail)
	if err != nil {
		return store.AlertIncident{}, "", err
	}
	outcome := "joined"
	row, err := q.FiringAlertIncident(ctx, FiringAlertIncidentParams{OrganisationID: occ.OrganisationID, ProjectID: occ.ProjectID, RuleID: occ.RuleID, Subject: occ.Subject})
	if errors.Is(err, pgx.ErrNoRows) {
		outcome = "reopened"
		row, err = q.ResolvedAlertIncidentSince(ctx, ResolvedAlertIncidentSinceParams{OrganisationID: occ.OrganisationID, ProjectID: occ.ProjectID, RuleID: occ.RuleID, Subject: occ.Subject, Since: ts(flapSince)})
	}
	switch {
	case err == nil:
		row, err = q.CountAlertOccurrence(ctx, CountAlertOccurrenceParams{ID: row.ID, LastOccurrenceAt: timestamp(occ.At), LastDetail: detail})
	case errors.Is(err, pgx.ErrNoRows):
		outcome = "opened"
		row, err = q.OpenAlertIncident(ctx, OpenAlertIncidentParams{
			OrganisationID: occ.OrganisationID, ProjectID: occ.ProjectID, RuleID: occ.RuleID, Subject: occ.Subject,
			SubjectLabel: label, OpenedAt: ts(now), LastOccurrenceAt: timestamp(occ.At), LastDetail: detail,
		})
	}
	if err != nil {
		return store.AlertIncident{}, "", fmt.Errorf("alert incident: %w", err)
	}
	if err := q.LinkAlertOccurrence(ctx, LinkAlertOccurrenceParams{IncidentID: row.ID, OrganisationID: occ.OrganisationID, ProjectID: occ.ProjectID, RuleID: occ.RuleID, OccurrenceID: occ.OccurrenceID}); err != nil {
		return store.AlertIncident{}, "", err
	}
	inc, err := alertIncident(row)
	if err != nil {
		return inc, "", err
	}
	if outcome == "opened" {
		if inc, _, err = s.queueChange(ctx, q, inc, "firing", now, compose); err != nil {
			return inc, "", err
		}
	}
	return inc, outcome, tx.Commit(ctx)
}

// queueChange queues one change of an incident to every channel its rule sends to, and returns how
// many deliveries it queued. A rule that is off, or sends nowhere, queues nothing and the change
// takes no number. A firing or reminder records the count it carried.
func (s *Store) queueChange(ctx context.Context, q *Queries, inc store.AlertIncident, change string, now time.Time, compose store.AlertCompose) (store.AlertIncident, int, error) {
	targets, err := q.AlertRuleTargets(ctx, AlertRuleTargetsParams{OrganisationID: inc.OrganisationID, ProjectID: inc.ProjectID, RuleID: inc.RuleID})
	if err != nil {
		return inc, 0, fmt.Errorf("alert rule targets: %w", err)
	}
	if !targets.Enabled || len(targets.Channels) == 0 {
		return inc, 0, nil
	}
	id, err := uuid(inc.ID)
	if err != nil {
		return inc, 0, err
	}
	prior := inc.NotifiedOccurrences
	row, err := q.NextAlertChange(ctx, NextAlertChangeParams{ID: id, Notified: change != "resolved", At: ts(now)})
	if err != nil {
		return inc, 0, fmt.Errorf("alert change: %w", err)
	}
	if inc, err = alertIncident(row); err != nil {
		return inc, 0, err
	}
	inc.SinceLastMessage = inc.Occurrences - prior
	for _, channel := range targets.Channels {
		msg, err := compose(inc, change, channel)
		if err != nil {
			return inc, 0, err
		}
		key := inc.ID + "/" + change + "-" + strconv.Itoa(inc.Changes) + "/" + channel
		if err := q.InsertAlertDelivery(ctx, InsertAlertDeliveryParams{OrganisationID: inc.OrganisationID, ProjectID: inc.ProjectID, IncidentID: id, Channel: channel, Change: change, IdempotencyKey: key, Message: msg}); err != nil {
			return inc, 0, fmt.Errorf("alert delivery: %w", err)
		}
	}
	return inc, len(targets.Channels), nil
}

// FiringAlertIncidents lists firing incidents across projects, oldest first.
func (s *Store) FiringAlertIncidents(ctx context.Context, limit int) ([]store.AlertIncident, error) {
	rows, err := s.q.FiringAlertIncidents(ctx, capRows(limit))
	if err != nil {
		return nil, fmt.Errorf("firing alert incidents: %w", err)
	}
	out := make([]store.AlertIncident, 0, len(rows))
	for _, r := range rows {
		inc, err := alertIncident(r)
		if err != nil {
			return nil, err
		}
		out = append(out, inc)
	}
	return out, nil
}

// NotifyAlertIncident queues a firing or reminder change of a firing incident, and returns how
// many deliveries it queued.
func (s *Store) NotifyAlertIncident(ctx context.Context, incidentID, change string, now time.Time, compose store.AlertCompose) (int, error) {
	id, err := uuid(incidentID)
	if err != nil {
		return 0, err
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return 0, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	row, err := q.AlertIncidentForUpdate(ctx, id)
	if err != nil {
		return 0, fmt.Errorf("alert incident %s: %w", incidentID, err)
	}
	if row.State != "firing" {
		return 0, nil
	}
	inc, err := alertIncident(row)
	if err != nil {
		return 0, err
	}
	_, n, err := s.queueChange(ctx, q, inc, change, now, compose)
	if err != nil {
		return 0, err
	}
	return n, tx.Commit(ctx)
}

// ResolveAlertIncident resolves a firing incident because its resolve rule held, and queues a
// resolved change when a firing message went out. It reports false if the incident was not firing.
func (s *Store) ResolveAlertIncident(ctx context.Context, incidentID string, now time.Time, compose store.AlertCompose) (bool, error) {
	id, err := uuid(incidentID)
	if err != nil {
		return false, err
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return false, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	row, err := q.ResolveAlertIncident(ctx, ResolveAlertIncidentParams{ID: id, At: ts(now), ResolvedBy: text("condition")})
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("resolve alert incident: %w", err)
	}
	inc, err := alertIncident(row)
	if err != nil {
		return false, err
	}
	if inc.NotifiedAt != nil {
		if _, _, err := s.queueChange(ctx, q, inc, "resolved", now, compose); err != nil {
			return false, err
		}
	}
	return true, tx.Commit(ctx)
}

// HoldAlertIncident records why a firing incident stays firing, and since when its source has
// been silent; nil clears either.
func (s *Store) HoldAlertIncident(ctx context.Context, incidentID string, reason *string, silentSince *time.Time) error {
	id, err := uuid(incidentID)
	if err != nil {
		return err
	}
	return s.q.HoldAlertIncident(ctx, HoldAlertIncidentParams{ID: id, HoldReason: textOf(reason), SilentSince: timestamp(silentSince)})
}

// AlertCallWindow counts one agent's evaluated calls ended since a time, and those with an active
// occurrence of issueID.
func (s *Store) AlertCallWindow(ctx context.Context, organisationID, projectID, issueID, agent string, since time.Time) (store.AlertCallWindow, error) {
	r, err := s.q.AlertCallWindow(ctx, AlertCallWindowParams{OrganisationID: organisationID, ProjectID: projectID, IssueID: issueID, AgentKey: pgtype.Text{String: agent, Valid: true}, Since: ts(since)})
	if err != nil {
		return store.AlertCallWindow{}, fmt.Errorf("alert call window: %w", err)
	}
	return store.AlertCallWindow{Calls: int(r.Calls), Matched: int(r.Matched), LastCallAt: timeOf(r.LastCallAt)}, nil
}

// AlertJobWindow counts one agent's jobs dispatched after a time.
func (s *Store) AlertJobWindow(ctx context.Context, organisationID, projectID, agent string, after time.Time) (store.AlertJobWindow, error) {
	r, err := s.q.AlertJobWindow(ctx, AlertJobWindowParams{OrganisationID: organisationID, ProjectID: projectID, AgentName: pgtype.Text{String: agent, Valid: true}, After: ts(after)})
	if err != nil {
		return store.AlertJobWindow{}, fmt.Errorf("alert job window: %w", err)
	}
	return store.AlertJobWindow{Succeeded: int(r.Succeeded), JoinedInTime: int(r.JoinedInTime), LastSeenAt: timeOf(r.LastSeenAt)}, nil
}

// AlertSource reads when a layer was last read well; a layer never read is the zero AlertSource.
func (s *Store) AlertSource(ctx context.Context, organisationID, projectID, layer string) (store.AlertSource, error) {
	r, err := s.q.AlertSourceRead(ctx, AlertSourceReadParams{OrganisationID: organisationID, ProjectID: projectID, Layer: layer})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.AlertSource{}, nil
	}
	if err != nil {
		return store.AlertSource{}, fmt.Errorf("alert source %s: %w", layer, err)
	}
	return store.AlertSource{GoodAt: timeOf(r.GoodAt), FreshUntil: timeOf(r.FreshUntil)}, nil
}

// ClaimAlertDeliveries leases up to limit due deliveries until leaseUntil.
func (s *Store) ClaimAlertDeliveries(ctx context.Context, now, leaseUntil time.Time, limit int) ([]store.AlertDelivery, error) {
	rows, err := s.q.ClaimAlertDeliveries(ctx, ClaimAlertDeliveriesParams{Now: ts(now), LeaseUntil: ts(leaseUntil), MaxRows: capRows(limit)})
	if err != nil {
		return nil, fmt.Errorf("claim alert deliveries: %w", err)
	}
	out := make([]store.AlertDelivery, 0, len(rows))
	for _, r := range rows {
		out = append(out, store.AlertDelivery{
			ID: r.ID.String(), OrganisationID: r.OrganisationID, ProjectID: r.ProjectID, IncidentID: uuidStr(r.IncidentID),
			Channel: r.Channel, Change: r.Change, Key: r.IdempotencyKey, Message: r.Message, Attempts: int(r.Attempts),
			CreatedAt: r.CreatedAt.Time.UTC(),
		})
	}
	return out, nil
}

func alertChannelSealContext(organisationID, projectID, kind string) string {
	return "alert-channel:" + organisationID + "/" + projectID + "/" + kind
}

// AlertChannelConfig opens a channel's configuration, or store.ErrAlertChannelMissing.
func (s *Store) AlertChannelConfig(ctx context.Context, organisationID, projectID, kind string) ([]byte, error) {
	sealed, err := s.q.SealedAlertChannel(ctx, SealedAlertChannelParams{OrganisationID: organisationID, ProjectID: projectID, Kind: kind})
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, store.ErrAlertChannelMissing
	}
	if err != nil {
		return nil, fmt.Errorf("alert channel %s: %w", kind, err)
	}
	plain, err := s.sealer.Open(sealed, alertChannelSealContext(organisationID, projectID, kind))
	if err != nil {
		return nil, fmt.Errorf("alert channel %s: %w", kind, err)
	}
	return plain, nil
}

// FinishAlertAttempt records one attempt and its outcome, marks or clears the channel broken, and
// lists a failed delivery on the Issues page.
func (s *Store) FinishAlertAttempt(ctx context.Context, d store.AlertDelivery, a store.AlertAttempt) error {
	id, err := uuid(d.ID)
	if err != nil {
		return err
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	attempts := capRows(d.Attempts + 1)
	if err := q.RecordAlertAttempt(ctx, RecordAlertAttemptParams{DeliveryID: id, Attempt: attempts, At: ts(a.At), Status: a.Status, Error: textOf(a.Error)}); err != nil {
		return fmt.Errorf("alert attempt: %w", err)
	}
	n, err := q.FinishAlertAttempt(ctx, FinishAlertAttemptParams{ID: id, State: a.State, Attempts: attempts, NextAttemptAt: ts(a.NextAttemptAt), LastStatus: text(a.Status), LastError: textOf(a.Error), At: ts(a.At)})
	if err != nil {
		return fmt.Errorf("finish alert delivery: %w", err)
	}
	if n == 0 {
		// Cancelled while it was sent: the attempt is kept, the channel and Issues page untouched.
		return tx.Commit(ctx)
	}
	if a.State == "delivered" || a.Broken {
		if err := q.SetAlertChannelBroken(ctx, SetAlertChannelBrokenParams{OrganisationID: d.OrganisationID, ProjectID: d.ProjectID, Kind: d.Channel, Broken: a.Broken}); err != nil {
			return err
		}
	}
	if a.Issue != "" {
		if err := q.EnsureAlertDeliveryIssue(ctx, EnsureAlertDeliveryIssueParams{OrganisationID: d.OrganisationID, ProjectID: d.ProjectID}); err != nil {
			return err
		}
		detail, err := json.Marshal(store.AlertDetail{Where: d.Channel, Detail: a.Issue})
		if err != nil {
			return err
		}
		n, err := q.InsertAlertDeliveryOccurrence(ctx, InsertAlertDeliveryOccurrenceParams{OrganisationID: d.OrganisationID, ProjectID: d.ProjectID, SourceID: "delivery:" + d.ID, OccurredAt: ts(a.At), Detail: detail})
		if err != nil {
			return err
		}
		if n > 0 {
			if err := q.AdvanceIssueEvidence(ctx, AdvanceIssueEvidenceParams{OrganisationID: d.OrganisationID, ProjectID: d.ProjectID, ID: "alert-not-delivered"}); err != nil {
				return err
			}
			if err := q.AdvanceIssueRevision(ctx, AdvanceIssueRevisionParams{OrganisationID: d.OrganisationID, ProjectID: d.ProjectID}); err != nil {
				return err
			}
		}
	}
	return tx.Commit(ctx)
}

// PruneAlerts deletes incidents resolved, and test deliveries made, before historyBefore, and
// occurrence marks older than seenBefore.
func (s *Store) PruneAlerts(ctx context.Context, historyBefore, seenBefore time.Time) error {
	if _, err := s.q.DeleteOldAlertIncidents(ctx, ts(historyBefore)); err != nil {
		return fmt.Errorf("prune alert incidents: %w", err)
	}
	if _, err := s.q.DeleteOldAlertTests(ctx, ts(historyBefore)); err != nil {
		return fmt.Errorf("prune alert tests: %w", err)
	}
	if _, err := s.q.DeleteOldAlertOccurrences(ctx, ts(seenBefore)); err != nil {
		return fmt.Errorf("prune alert occurrences: %w", err)
	}
	return nil
}

// AlertRules returns all seven rules' setup, saved or default (on, sending nowhere).
func (s *Store) AlertRules(ctx context.Context, organisationID, projectID string) ([]store.AlertRule, error) {
	rows, err := s.q.ListAlertRules(ctx, ListAlertRulesParams{OrganisationID: organisationID, ProjectID: projectID})
	if err != nil {
		return nil, fmt.Errorf("alert rules: %w", err)
	}
	out := make([]store.AlertRule, 0, len(store.AlertRuleIDs))
	for _, id := range store.AlertRuleIDs {
		r := store.AlertRule{RuleID: id, On: true, To: []string{}}
		if i := slices.IndexFunc(rows, func(x ListAlertRulesRow) bool { return x.RuleID == id }); i >= 0 {
			r.On, r.To = rows[i].Enabled, nonNilStrings(rows[i].Channels)
		}
		out = append(out, r)
	}
	return out, nil
}

// PutAlertRule saves one rule's setup. An unknown project is store.ErrNotFound.
func (s *Store) PutAlertRule(ctx context.Context, organisationID, projectID string, r store.AlertRule) (store.AlertRule, error) {
	row, err := s.q.UpsertAlertRule(ctx, UpsertAlertRuleParams{OrganisationID: organisationID, ProjectID: projectID, RuleID: r.RuleID, Enabled: r.On, Channels: nonNilStrings(r.To)})
	if isForeignKey(err) {
		return store.AlertRule{}, store.ErrNotFound
	}
	if err != nil {
		return store.AlertRule{}, fmt.Errorf("save alert rule: %w", err)
	}
	return store.AlertRule{RuleID: row.RuleID, On: row.Enabled, To: nonNilStrings(row.Channels)}, nil
}

// AlertChannels lists a project's configured channels with their latest delivery.
func (s *Store) AlertChannels(ctx context.Context, organisationID, projectID string) ([]store.AlertChannel, error) {
	rows, err := s.q.ListAlertChannels(ctx, ListAlertChannelsParams{OrganisationID: organisationID, ProjectID: projectID})
	if err != nil {
		return nil, fmt.Errorf("alert channels: %w", err)
	}
	out := make([]store.AlertChannel, 0, len(rows))
	for _, r := range rows {
		c := store.AlertChannel{Kind: r.Kind, Target: r.Target, Broken: r.Broken, UpdatedAt: r.UpdatedAt.Time.UTC()}
		if r.DeliveryID.Valid {
			c.Last = &store.AlertDeliveryStatus{
				ID: r.DeliveryID.String(), Channel: r.Kind, Change: r.Change, State: r.State, Attempts: int(r.Attempts),
				NextAttemptAt: timeOf(r.NextAttemptAt), LastStatus: strOf(r.LastStatus), LastError: strOf(r.LastError),
				DeliveredAt: timeOf(r.DeliveredAt), CreatedAt: r.DeliveryCreatedAt.Time.UTC(),
			}
		}
		out = append(out, c)
	}
	return out, nil
}

// PutAlertChannel seals and saves a channel's configuration, clearing broken. An unknown project is
// store.ErrNotFound.
func (s *Store) PutAlertChannel(ctx context.Context, organisationID, projectID, kind string, config []byte, target string) error {
	sealed, err := s.sealer.Seal(config, alertChannelSealContext(organisationID, projectID, kind))
	if err != nil {
		return fmt.Errorf("seal alert channel: %w", err)
	}
	err = s.q.UpsertAlertChannel(ctx, UpsertAlertChannelParams{OrganisationID: organisationID, ProjectID: projectID, Kind: kind, ConfigSealed: sealed, Target: target})
	if isForeignKey(err) {
		return store.ErrNotFound
	}
	if err != nil {
		return fmt.Errorf("save alert channel: %w", err)
	}
	return nil
}

// DeleteAlertChannel removes a channel and cancels its queued deliveries; one not configured is
// store.ErrNotFound.
func (s *Store) DeleteAlertChannel(ctx context.Context, organisationID, projectID, kind string) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	n, err := q.DeleteAlertChannel(ctx, DeleteAlertChannelParams{OrganisationID: organisationID, ProjectID: projectID, Kind: kind})
	if err != nil {
		return fmt.Errorf("delete alert channel: %w", err)
	}
	if n == 0 {
		return store.ErrNotFound
	}
	if err := q.CancelAlertDeliveries(ctx, CancelAlertDeliveriesParams{OrganisationID: organisationID, ProjectID: projectID, Channel: kind}); err != nil {
		return fmt.Errorf("cancel alert deliveries: %w", err)
	}
	return tx.Commit(ctx)
}

// QueueAlertTest queues a test delivery to a configured channel, or store.ErrAlertChannelMissing.
func (s *Store) QueueAlertTest(ctx context.Context, organisationID, projectID, kind, key string, message json.RawMessage) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	if _, err := q.SealedAlertChannel(ctx, SealedAlertChannelParams{OrganisationID: organisationID, ProjectID: projectID, Kind: kind}); errors.Is(err, pgx.ErrNoRows) {
		return store.ErrAlertChannelMissing
	} else if err != nil {
		return err
	}
	if err := q.InsertAlertDelivery(ctx, InsertAlertDeliveryParams{OrganisationID: organisationID, ProjectID: projectID, Channel: kind, Change: "test", IdempotencyKey: key, Message: message}); err != nil {
		return fmt.Errorf("alert test delivery: %w", err)
	}
	return tx.Commit(ctx)
}

// AlertIncidents lists incidents open in or across a range, newest first, with their deliveries.
func (s *Store) AlertIncidents(ctx context.Context, organisationID, projectID string, from, to time.Time, limit int) ([]store.AlertIncident, map[string][]store.AlertDeliveryStatus, error) {
	rows, err := s.q.ListAlertIncidents(ctx, ListAlertIncidentsParams{OrganisationID: organisationID, ProjectID: projectID, FromAt: ts(from), ToAt: ts(to), MaxRows: capRows(limit)})
	if err != nil {
		return nil, nil, fmt.Errorf("alert incidents: %w", err)
	}
	incidents := make([]store.AlertIncident, 0, len(rows))
	ids := make([]pgtype.UUID, 0, len(rows))
	for _, r := range rows {
		inc, err := alertIncident(r)
		if err != nil {
			return nil, nil, err
		}
		incidents = append(incidents, inc)
		ids = append(ids, r.ID)
	}
	deliveries := map[string][]store.AlertDeliveryStatus{}
	if len(ids) == 0 {
		return incidents, deliveries, nil
	}
	drows, err := s.q.ListAlertIncidentDeliveries(ctx, ListAlertIncidentDeliveriesParams{OrganisationID: organisationID, ProjectID: projectID, IncidentIds: ids})
	if err != nil {
		return nil, nil, fmt.Errorf("alert deliveries: %w", err)
	}
	for _, d := range drows {
		incident := d.IncidentID.String()
		deliveries[incident] = append(deliveries[incident], store.AlertDeliveryStatus{
			ID: d.ID.String(), IncidentID: &incident, Channel: d.Channel, Change: d.Change, State: d.State, Attempts: int(d.Attempts),
			NextAttemptAt: timeOf(d.NextAttemptAt), LastStatus: strOf(d.LastStatus), LastError: strOf(d.LastError),
			DeliveredAt: timeOf(d.DeliveredAt), CreatedAt: d.CreatedAt.Time.UTC(),
		})
	}
	return incidents, deliveries, nil
}

// ResolveAlertIncidentByPerson resolves a firing recording-lost incident for a person: the
// recording is gone, so no condition resolves it and no resolved message is sent. Another rule's
// incident, or one not firing, is store.ErrAlertIncidentState; an unknown one store.ErrNotFound.
func (s *Store) ResolveAlertIncidentByPerson(ctx context.Context, organisationID, projectID, incidentID, userID string, now time.Time) (store.AlertIncident, error) {
	id, err := uuid(incidentID)
	if err != nil {
		return store.AlertIncident{}, store.ErrNotFound
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return store.AlertIncident{}, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	row, err := q.AlertIncidentInProject(ctx, AlertIncidentInProjectParams{OrganisationID: organisationID, ProjectID: projectID, ID: id})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.AlertIncident{}, store.ErrNotFound
	}
	if err != nil {
		return store.AlertIncident{}, fmt.Errorf("alert incident: %w", err)
	}
	if row.State != "firing" || row.RuleID != "recording-lost" {
		return store.AlertIncident{}, store.ErrAlertIncidentState
	}
	row, err = q.ResolveAlertIncident(ctx, ResolveAlertIncidentParams{ID: id, At: ts(now), ResolvedBy: text(userID)})
	if err != nil {
		return store.AlertIncident{}, fmt.Errorf("resolve alert incident: %w", err)
	}
	inc, err := alertIncident(row)
	if err != nil {
		return inc, err
	}
	return inc, tx.Commit(ctx)
}

// AlertEvents returns what each event rule matched in a range, newest first, and how many trunks
// carry plain RTP now.
func (s *Store) AlertEvents(ctx context.Context, organisationID, projectID string, from, to time.Time, limit int) ([]store.AlertEvent, int, error) {
	rows, err := s.q.AlertEventOccurrences(ctx, AlertEventOccurrencesParams{OrganisationID: organisationID, ProjectID: projectID, FromAt: ts(from), ToAt: ts(to), MaxRows: capRows(limit)})
	if err != nil {
		return nil, 0, fmt.Errorf("alert events: %w", err)
	}
	out := make([]store.AlertEvent, 0, len(rows))
	for _, r := range rows {
		out = append(out, store.AlertEvent{RuleID: r.RuleID, At: timeOf(r.OccurredAt), AlertDetail: store.AlertDetail{Where: r.Location, Detail: r.Detail, CallID: uuidStr(r.CallID)}})
	}
	trunks, err := s.q.CountPlainPhoneTrunks(ctx, CountPlainPhoneTrunksParams{OrganisationID: organisationID, ProjectID: projectID})
	if err != nil {
		return nil, 0, fmt.Errorf("plain-phone trunks: %w", err)
	}
	return out, int(trunks), nil
}

// isForeignKey reports a row naming a missing project.
func isForeignKey(err error) bool {
	var pgErr *pgconn.PgError
	return errors.As(err, &pgErr) && pgErr.Code == foreignKeyViolation
}

// SendingAlertRules returns the org/project/rule keys of rules that would send a change now.
func (s *Store) SendingAlertRules(ctx context.Context) (map[string]bool, error) {
	rows, err := s.q.SendingAlertRules(ctx)
	if err != nil {
		return nil, fmt.Errorf("sending alert rules: %w", err)
	}
	out := make(map[string]bool, len(rows))
	for _, r := range rows {
		out[r.OrganisationID+"/"+r.ProjectID+"/"+r.RuleID] = true
	}
	return out, nil
}
