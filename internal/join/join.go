package join

import (
	"context"
	"log/slog"
	"time"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// Calls is the call index.
type Calls interface {
	UpsertCall(ctx context.Context, c store.CallUpsert) (string, error)
	UpsertCallByJob(ctx context.Context, organisationID, projectID, jobID string, activityAt *time.Time) (string, error)
	CallByRoom(ctx context.Context, organisationID, projectID, roomSID string) (store.CallRef, error)
	CallByJob(ctx context.Context, organisationID, projectID, jobID string) (store.CallRef, error)
	CallInRoom(ctx context.Context, organisationID, projectID, roomName string, at time.Time) (string, error)
	TouchCall(ctx context.Context, callID, jobID string, startedAt, activityAt *time.Time) error
	FinishCall(ctx context.Context, callID string, endedAt *time.Time) error
	UpsertParticipant(ctx context.Context, p store.Participant) error
	CloseIdleCalls(ctx context.Context, idleSince time.Time) (int64, error)
}

// Traces counts traces that never linked to a call.
type Traces interface {
	CountUnlinkedTraces(ctx context.Context, lastSpanBefore time.Time) (uint64, error)
}

// Joiner joins events to calls.
type Joiner struct {
	Calls  Calls
	Traces Traces
	Log    *slog.Logger
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
// means the call index did not answer; a row that resolves to nothing is not an error.
func (j *Joiner) Join(ctx context.Context, rows []store.Event) error {
	for i := range rows {
		r := &rows[i]
		r.CallID, r.Spans, r.TraceCalls, r.Unmatched = "", nil, nil, nil
		var err error
		switch event.Kind(r.Kind) {
		case event.KindLiveKitWebhook:
			err = j.webhook(ctx, r)
		case event.KindAgentSessionReport:
			err = j.sessionReport(ctx, r)
		case event.KindAgentTraces:
			err = j.traces(ctx, r)
		}
		if err != nil {
			return err
		}
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

// ptr returns a pointer to t, or nil when t is zero: an unreported time.
func ptr(t time.Time) *time.Time {
	if t.IsZero() {
		return nil
	}
	return &t
}

// latest returns the later of two optional times.
func latest(a, b *time.Time) *time.Time {
	if a == nil || (b != nil && b.After(*a)) {
		return b
	}
	return a
}
