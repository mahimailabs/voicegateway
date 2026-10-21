package join

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/turn"
)

// Calls is the call index.
type Calls interface {
	PendingMediaBackfills(context.Context) ([]store.MediaBackfill, error)
	AdvanceMediaBackfill(context.Context, store.MediaBackfill, int64, string, bool) error
	DeferMediaBackfill(context.Context, store.MediaBackfill) error
	PutMediaFinding(context.Context, store.MediaFinding) error
	PendingCallFindings(context.Context, int) ([]store.PendingFindings, error)
	PutCallFindings(context.Context, store.PendingFindings, []store.Finding, []string, int64, bool, time.Time) error
	DeferCallFindings(context.Context, store.PendingFindings) error
	ProjectSettings(context.Context, string, string) (store.ProjectSettings, error)
	UpsertCall(ctx context.Context, c store.CallUpsert) (string, error)
	UpsertCallByJob(ctx context.Context, organisationID, projectID, jobID string, activityAt, firstSeenAt *time.Time) (string, error)
	CallByRoom(ctx context.Context, organisationID, projectID, roomSID string) (store.CallRef, error)
	CallByJob(ctx context.Context, organisationID, projectID, jobID string) (store.CallRef, error)
	CallInRoom(ctx context.Context, organisationID, projectID, roomName string, at time.Time) (string, error)
	TouchCall(ctx context.Context, callID, jobID string, startedAt, activityAt, firstSeenAt *time.Time) error
	FinishCall(ctx context.Context, callID string, endedAt *time.Time) error
	UpsertParticipant(ctx context.Context, p store.Participant) error
	CloseIdleCalls(ctx context.Context, idleSince time.Time) (int64, error)
	StaleCalls(ctx context.Context, limit int) ([]store.StaleCall, error)
	MarkStale(ctx context.Context, callIDs []string) error
	DeferStale(ctx context.Context, callID string) error
	PutSummary(ctx context.Context, c store.StaleCall, sum store.CallSummary, computedAt time.Time) error
	LinkRecording(ctx context.Context, l store.RecordingLink) error
	// MergeCallFields adds an SDK's custom fields to a call; a key it already has takes the new value.
	MergeCallFields(ctx context.Context, callID string, fields map[string]string) error
}

// Traces counts traces that never linked to a call, and reads a call's spans and the turns built
// from them, for its summary.
type Traces interface {
	MediaEvents(context.Context, store.MediaBackfill) ([]store.Event, error)
	CountUnlinkedTraces(ctx context.Context, lastSpanBefore time.Time) (uint64, error)
	CallTrace(ctx context.Context, organisationID, projectID, callID string) ([]store.Span, turn.Result, error)
}

// Joiner joins events to calls.
type Joiner struct {
	// Changed invalidates console reads only after a rebuilt summary is stored.
	Changed func(context.Context, string, string)
	Calls   Calls
	Traces  Traces
	Log     *slog.Logger
	// Timeout bounds the call-index writes for one row, so a large batch is not held to one
	// deadline and a hung Postgres is retried, not waited on.
	Timeout time.Duration
}

// Reasons recorded in the unmatched table.
const (
	reasonUndecodable = "undecodable"
	reasonNoRoom      = "no room or job id"
	reasonNoCall      = "no call for its room or job"
)

// Join resolves each row to its call, writing the call index as it goes, and sets the row's call
// id, spans, trace links and unmatched records. It may be called again with the same rows after a
// failure: every write it makes is an upsert, and the fields it sets are reset first. An error
// wrapping store.ErrBadRow means a row's values were refused; any other error means the call index
// did not answer. A row that resolves to nothing is not an error.
func (j *Joiner) Join(ctx context.Context, rows []store.Event) error {
	for i := range rows {
		if err := j.row(ctx, &rows[i]); err != nil {
			return fmt.Errorf("join %s: %w", rows[i].StreamID, err)
		}
	}
	return nil
}

func (j *Joiner) row(ctx context.Context, r *store.Event) error {
	r.CallID, r.Spans, r.TraceCalls, r.Unmatched, r.MetricPoints = "", nil, nil, nil, nil
	if j.Timeout > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, j.Timeout)
		defer cancel()
	}
	switch event.Kind(r.Kind) {
	case event.KindLiveKitWebhook:
		return j.webhook(ctx, r)
	case event.KindAgentSessionReport:
		return j.sessionReport(ctx, r)
	case event.KindAgentTraces:
		return j.traces(ctx, r)
	case event.KindAgentMetrics:
		return j.metrics(ctx, r)
	case event.KindSDKEvents:
		return j.sdkBatch(ctx, r)
	}
	return nil
}

// unmatched records that r resolved to no call, and logs its kind and project, never its body.
func (j *Joiner) unmatched(r *store.Event, reason string) {
	r.Unmatched = append(r.Unmatched, store.Unmatched{Kind: r.Kind, Key: r.DedupeKey, Reason: reason})
	j.Log.Warn("join: event matched no call", "kind", r.Kind, "project", r.ProjectID, "reason", reason)
}

// LinkWindow is how long after its last span a trace may stay unlinked before it is counted.
const LinkWindow = 15 * time.Minute

// IdleTimeout is how long an open call goes with no activity before it is closed as inferred.
const IdleTimeout = 2 * time.Hour

// Sweep closes idle calls as inferred and counts unlinked traces, as of now.
func (j *Joiner) Sweep(ctx context.Context, now time.Time) error {
	closed, err := j.Calls.CloseIdleCalls(ctx, now.Add(-IdleTimeout))
	if err != nil {
		return err
	}
	if closed > 0 {
		j.Log.Info("join: closed idle calls as inferred", "count", closed)
	}
	counted, err := j.Traces.CountUnlinkedTraces(ctx, now.Add(-LinkWindow))
	if err != nil {
		return err
	}
	if counted > 0 {
		j.Log.Warn("join: traces matched no call", "count", counted)
	}
	return nil
}

// SummaryBatch is the most summaries one sweep rebuilds, oldest mark first.
const SummaryBatch = 200

// Stored marks the summaries of every call the rows joined stale, once the rows are in ClickHouse.
func (j *Joiner) Stored(ctx context.Context, rows []store.Event) error {
	if err := j.mediaFindings(ctx, rows); err != nil {
		return err
	}
	seen := map[string]bool{}
	var ids []string
	add := func(id string) {
		if id != "" && !seen[id] {
			seen[id] = true
			ids = append(ids, id)
		}
	}
	for _, r := range rows {
		add(r.CallID)
		for _, sp := range r.Spans {
			add(sp.CallID)
		}
		for _, p := range r.MetricPoints {
			add(p.CallID)
		}
	}
	return j.Calls.MarkStale(ctx, ids)
}

// Summarize rebuilds the summaries of calls marked stale, at most SummaryBatch, as of now. A call
// joined to again while its summary was rebuilt stays marked, for the next sweep. A call whose
// rebuild fails is skipped, counted and moved to the back of the queue, so it cannot hold up the
// others; the sweep fails only when the queue cannot be read.
func (j *Joiner) Summarize(ctx context.Context, now time.Time) error {
	stale, err := j.Calls.StaleCalls(ctx, SummaryBatch)
	if err != nil {
		return err
	}
	failed := 0
	changed := make(map[[2]string]bool)
	for _, c := range stale {
		if err := j.summarizeOne(ctx, c, now); err != nil {
			failed++
			j.Log.Error("join: call summary failed, retrying it after the others", "call", c.ID, "err", err)
			if err := j.Calls.DeferStale(ctx, c.ID); err != nil {
				j.Log.Error("join: could not back off a failed summary", "call", c.ID, "err", err)
			}
		} else {
			changed[[2]string{c.OrganisationID, c.ProjectID}] = true
		}
	}
	if j.Changed != nil {
		notifyCtx, cancel := context.WithTimeout(ctx, 250*time.Millisecond)
		for scope := range changed {
			j.Changed(notifyCtx, scope[0], scope[1])
		}
		cancel()
	}
	if len(stale) > 0 {
		j.Log.Debug("join: rebuilt call summaries", "count", len(stale)-failed, "failed", failed)
	}
	return nil
}

func (j *Joiner) summarizeOne(ctx context.Context, c store.StaleCall, now time.Time) error {
	spans, res, err := j.Traces.CallTrace(ctx, c.OrganisationID, c.ProjectID, c.ID)
	if err != nil {
		return err
	}
	s := turn.Summarize(spans, res)
	var findings []store.Finding
	var settingsRevision int64
	if len(spans) > 0 || res.NoReply {
		settings, err := j.Calls.ProjectSettings(ctx, c.OrganisationID, c.ProjectID)
		if err != nil {
			return fmt.Errorf("read finding thresholds: %w", err)
		}
		settingsRevision = settings.Revision
		observed := turn.Findings(spans, res, settings.Thresholds)
		if len(observed) > 0 || len(res.Turns) > 0 || res.NoReply {
			findings = observed
		}
	}
	return j.Calls.PutSummary(ctx, c, store.CallSummary{
		Findings: findings, EvaluatedFindingIDs: turn.FindingCoverage(res), FindingSettingsRevision: settingsRevision, FindingSourceAvailable: len(spans) > 0 || res.NoReply, TurnCount: s.TurnCount, AnswerMs: s.AnswerMs, AnswerMsMedian: s.AnswerMsMedian,
		Models: s.Models, Pipeline: s.Pipeline, AgentKey: s.AgentKey,
		RoomName: s.RoomName, FirstSeenAt: s.FirstSeenAt,
	}, now)
}

// ptr returns a pointer to t, or nil when t is zero: an unreported time.
func ptr(t time.Time) *time.Time {
	if t.IsZero() {
		return nil
	}
	return &t
}

// earliest returns the earlier of two optional times.
func earliest(a, b *time.Time) *time.Time {
	if a == nil || (b != nil && b.Before(*a)) {
		return b
	}
	return a
}

// latest returns the later of two optional times.
func latest(a, b *time.Time) *time.Time {
	if a == nil || (b != nil && b.After(*a)) {
		return b
	}
	return a
}
