package alert

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// The behaviour alerts-behaviour.md fixes (every Recommended line accepted on 2026-10-16).
const (
	// lookback bounds how far back the evaluator reads newly stored occurrences.
	lookback = 24 * time.Hour
	// flapGuard reopens an incident resolved this recently instead of opening a new one.
	flapGuard = 10 * time.Minute
	// callWindow and minCalls: a call rule resolves after a window of calls with none matching,
	// with at least minCalls seen in it.
	callWindow = 30 * time.Minute
	minCalls   = 5
	// joinsInTime consecutive joins within the threshold resolve slow-join.
	joinsInTime = 3
	// reminderEvery spaces reminders of one incident.
	reminderEvery = 4 * time.Hour
	// silentAfter marks a source silent when it has reported nothing for this long.
	silentAfter = 30 * time.Minute
	// history keeps incidents and deliveries (the 30-day default); seenFor keeps the marks that
	// stop a re-stored occurrence counting twice, as long as raw call data lives (90 days).
	history = 30 * 24 * time.Hour
	seenFor = 90 * 24 * time.Hour
	// pruneEvery spaces retention runs.
	pruneEvery = time.Hour
)

// batch caps the occurrences and incidents one tick handles; the rest wait for the next tick.
const batch = 500

const holdFewCalls = "fewer than 5 calls in the last 30 minutes"

// Evaluator decides incidents. One worker evaluates at a time (a Postgres advisory lock); every
// step is safe to repeat after a crash.
type Evaluator struct {
	Store   Store
	Ingress Ingress
	Compose store.AlertCompose
	Log     *slog.Logger

	lastPrune time.Time
}

// Tick counts new occurrences, then resolves, holds and reminds firing incidents.
func (e *Evaluator) Tick(ctx context.Context, now time.Time) error {
	release, ok, err := e.Store.TryAlertLock(ctx)
	if err != nil || !ok {
		return err
	}
	defer release()
	occs, err := e.Store.NewAlertOccurrences(ctx, now.Add(-lookback), batch)
	if err != nil {
		return err
	}
	for _, occ := range occs {
		if _, _, err := e.Store.ApplyAlertOccurrence(ctx, occ, label(occ.RuleID, occ.Subject), now, now.Add(-flapGuard), e.Compose); err != nil {
			return fmt.Errorf("alert occurrence %s/%s: %w", occ.RuleID, occ.OccurrenceID, err)
		}
	}
	standing, err := e.Store.StandingAlerts(ctx)
	if err != nil {
		return err
	}
	plain := map[string]bool{}
	for _, s := range standing {
		plain[s.OrganisationID+"/"+s.ProjectID+"/"+s.SourceID] = true
		// A trunk that turns plain again, or changes while plain, is a new occurrence: the Issues
		// page bumps observed_at only then.
		at := s.ObservedAt
		occ := store.AlertOccurrence{
			OrganisationID: s.OrganisationID, ProjectID: s.ProjectID, RuleID: "plain-phone",
			OccurrenceID: s.SourceID + "@" + s.ObservedAt.Format(time.RFC3339Nano), Subject: s.SourceID, At: &at,
			Detail: store.AlertDetail{Where: s.Where, Detail: s.Detail},
		}
		if _, _, err := e.Store.ApplyAlertOccurrence(ctx, occ, s.Where, now, now.Add(-flapGuard), e.Compose); err != nil {
			return fmt.Errorf("alert standing %s: %w", s.SourceID, err)
		}
	}
	firing, err := e.Store.FiringAlertIncidents(ctx, batch)
	if err != nil {
		return err
	}
	sending, err := e.Store.SendingAlertRules(ctx)
	if err != nil {
		return err
	}
	var errs []error
	for _, inc := range firing {
		if err := e.settle(ctx, inc, now, plain, sending[inc.OrganisationID+"/"+inc.ProjectID+"/"+inc.RuleID]); err != nil {
			errs = append(errs, fmt.Errorf("alert incident %s: %w", inc.ID, err))
		}
	}
	if now.Sub(e.lastPrune) >= pruneEvery {
		if err := e.Store.PruneAlerts(ctx, now.Add(-history), now.Add(-seenFor)); err != nil {
			errs = append(errs, err)
		} else {
			e.lastPrune = now
		}
	}
	return errors.Join(errs...)
}

// settle resolves a firing incident whose resolve rule holds, records why it stays firing
// otherwise, and, when its rule sends, what is due: the firing message never sent while the rule
// was off or had no channel, or a reminder.
func (e *Evaluator) settle(ctx context.Context, inc store.AlertIncident, now time.Time, plain map[string]bool, sending bool) error {
	resolved, hold, silent, err := e.check(ctx, inc, now, plain)
	if err != nil {
		return err
	}
	if resolved {
		_, err := e.Store.ResolveAlertIncident(ctx, inc.ID, now, e.Compose)
		return err
	}
	if !samePtr(hold, inc.HoldReason) || !sameTime(silent, inc.SilentSince) {
		if err := e.Store.HoldAlertIncident(ctx, inc.ID, hold, silent); err != nil {
			return err
		}
		inc.HoldReason, inc.SilentSince = hold, silent
	}
	switch {
	case !sending:
	case inc.NotifiedAt == nil:
		_, err = e.Store.NotifyAlertIncident(ctx, inc.ID, "firing", now, e.Compose)
	case reminderDue(inc, now):
		_, err = e.Store.NotifyAlertIncident(ctx, inc.ID, "reminder", now, e.Compose)
	}
	return err
}

// reminderDue: new occurrences since the last message, and 4 hours since it, or, for a high rule,
// the count has doubled since it.
func reminderDue(inc store.AlertIncident, now time.Time) bool {
	if inc.NotifiedAt == nil || inc.Occurrences <= inc.NotifiedOccurrences {
		return false
	}
	if now.Sub(*inc.NotifiedAt) >= reminderEvery {
		return true
	}
	return rules[inc.RuleID].severity == "high" && inc.NotifiedOccurrences > 0 && inc.Occurrences >= 2*inc.NotifiedOccurrences
}

// check applies a rule's resolve condition (alerts-behaviour.md, The seven rules). It never
// resolves on missing data: a silent source holds the incident firing and says since when.
func (e *Evaluator) check(ctx context.Context, inc store.AlertIncident, now time.Time, plain map[string]bool) (resolved bool, hold *string, silent *time.Time, err error) {
	after := inc.OpenedAt
	if inc.LastOccurrenceAt != nil {
		after = *inc.LastOccurrenceAt
	}
	switch inc.RuleID {
	case "slow-answer", "cut-off":
		w, err := e.Store.AlertCallWindow(ctx, inc.OrganisationID, inc.ProjectID, rules[inc.RuleID].issue, inc.Subject, now.Add(-callWindow))
		if err != nil {
			return false, nil, nil, err
		}
		if w.Calls >= minCalls && w.Matched == 0 {
			return true, nil, nil, nil
		}
		if w.Calls < minCalls {
			reason := holdFewCalls
			hold = &reason
		}
		return false, hold, silentSince(w.LastCallAt, inc.OpenedAt, now), nil
	case "job-failed", "slow-join":
		w, err := e.Store.AlertJobWindow(ctx, inc.OrganisationID, inc.ProjectID, inc.Subject, after)
		if err != nil {
			return false, nil, nil, err
		}
		if (inc.RuleID == "job-failed" && w.Succeeded > 0) || (inc.RuleID == "slow-join" && w.JoinedInTime >= joinsInTime) {
			return true, nil, nil, nil
		}
		return false, nil, silentSince(w.LastSeenAt, inc.OpenedAt, now), nil
	case "plain-phone":
		if !plain[inc.OrganisationID+"/"+inc.ProjectID+"/"+inc.Subject] {
			return true, nil, nil, nil
		}
		src, err := e.Store.AlertSource(ctx, inc.OrganisationID, inc.ProjectID, store.LayerSIP)
		if err != nil {
			return false, nil, nil, err
		}
		if src.FreshUntil != nil && src.FreshUntil.Before(now) {
			since := *src.FreshUntil
			if src.GoodAt != nil {
				since = *src.GoodAt
			}
			return false, nil, &since, nil
		}
		return false, nil, nil, nil
	case "ingress-error":
		if e.Ingress == nil {
			return false, nil, nil, nil
		}
		ok, err := e.ingressRecovered(ctx, inc, after)
		return ok, nil, nil, err
	}
	// recording-lost: the recording is gone; only a person resolves it.
	return false, nil, nil, nil
}

// ingressRecovered: an ingress_started webhook, or a ListIngress read, after the error reports the
// endpoint in a state other than ENDPOINT_ERROR.
func (e *Evaluator) ingressRecovered(ctx context.Context, inc store.AlertIncident, after time.Time) (bool, error) {
	ids := []string{inc.Subject}
	hooks, err := e.Ingress.IngressWebhooks(ctx, inc.OrganisationID, inc.ProjectID, ids, after)
	if err != nil {
		return false, err
	}
	for _, h := range hooks {
		at := h.ReceivedAt
		if h.At != nil {
			at = *h.At
		}
		if at.After(after) && h.Status != "ENDPOINT_ERROR" {
			return true, nil
		}
	}
	changes, err := e.Ingress.IngressChanges(ctx, inc.OrganisationID, inc.ProjectID, ids)
	if err != nil {
		return false, err
	}
	// The latest read after the error decides.
	var last *store.StatusChange
	for i := range changes {
		if changes[i].At.After(after) {
			last = &changes[i]
		}
	}
	return last != nil && last.Status != "ENDPOINT_ERROR", nil
}

// silentSince is when a source last reported, once that is longer ago than silentAfter. A source
// that never reported is silent since the incident opened.
func silentSince(last *time.Time, opened, now time.Time) *time.Time {
	if last == nil {
		if now.Sub(opened) < silentAfter {
			return nil
		}
		return &opened
	}
	if now.Sub(*last) < silentAfter {
		return nil
	}
	t := *last
	return &t
}

func samePtr(a, b *string) bool { return (a == nil && b == nil) || (a != nil && b != nil && *a == *b) }

func sameTime(a, b *time.Time) bool {
	return (a == nil && b == nil) || (a != nil && b != nil && a.Equal(*b))
}
