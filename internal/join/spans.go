package join

import (
	"cmp"
	"context"
	"errors"
	"slices"
	"time"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// The span attributes LiveKit Agents 1.8.7 stamps with a job's ids (livekit/agents/telemetry/
// traces.py): room_id and job_id on every span, lk.room_sid and lk.job_id on a few.
const (
	attrRoomID    = "room_id"
	attrJobID     = "job_id"
	attrLKRoomSID = "lk.room_sid"
	attrLKJobID   = "lk.job_id"
)

// ref is what a span names its call by: a room SID or, failing that, a job id.
type ref struct{ roomSID, jobID string }

// traces decodes an OTLP trace export into spans and joins each by its own ids. Spans with no id
// keep an empty call id and resolve through their trace when another span of it joins. The export's
// own row carries the call its spans joined when they all joined one; an export spanning several
// calls, or none, keeps an empty call id, since one row cannot name two.
func (j *Joiner) traces(ctx context.Context, r *store.Event) error {
	req := ptraceotlp.NewExportRequest()
	if err := req.UnmarshalProto(r.Payload); err != nil {
		j.unmatched(r, reasonUndecodable)
		return nil
	}
	r.Spans = decode(req.Traces())

	byRef := map[ref][]int{}
	var refs []ref
	for i, sp := range r.Spans {
		k := spanRef(sp.Attributes)
		if k == (ref{}) {
			continue
		}
		if _, ok := byRef[k]; !ok {
			refs = append(refs, k)
		}
		byRef[k] = append(byRef[k], i)
	}
	// Room SIDs first, so a job-only span earlier in the export finds the call its room opened
	// rather than opening a second one.
	slices.SortStableFunc(refs, func(a, b ref) int {
		return cmp.Compare(boolInt(a.roomSID == ""), boolInt(b.roomSID == ""))
	})
	linked := map[store.TraceCall]bool{}
	for _, k := range refs {
		callID, err := j.joinSpans(ctx, r, k, byRef[k])
		if err != nil {
			return err
		}
		if callID == "" {
			continue
		}
		for _, i := range byRef[k] {
			sp := &r.Spans[i]
			if sp.CallID == "" {
				continue
			}
			tc := store.TraceCall{TraceID: sp.TraceID, CallID: sp.CallID}
			if !linked[tc] {
				linked[tc] = true
				r.TraceCalls = append(r.TraceCalls, tc)
			}
		}
	}
	calls := map[string]bool{}
	for _, sp := range r.Spans {
		if sp.CallID != "" {
			calls[sp.CallID] = true
		}
	}
	if len(calls) == 1 {
		for id := range calls {
			r.CallID = id
		}
	}
	return nil
}

// staleAfter is how long after its call ended a span may start and still join it, for a job's
// shutdown spans and clock skew between the agent host and LiveKit. A span starting later carries
// a previous job's ids, which LiveKit Agents falls back to outside a job.
const staleAfter = time.Minute

// joinSpans joins the spans at idx, which share ref k, to their call, creating it if no source has.
// A span that starts more than staleAfter after that call ended is left unjoined.
func (j *Joiner) joinSpans(ctx context.Context, r *store.Event, k ref, idx []int) (string, error) {
	var (
		existing store.CallRef
		err      error
	)
	if k.roomSID != "" {
		existing, err = j.Calls.CallByRoom(ctx, r.OrganisationID, r.ProjectID, k.roomSID)
	} else {
		existing, err = j.Calls.CallByJob(ctx, r.OrganisationID, r.ProjectID, k.jobID)
	}
	if err != nil && !errors.Is(err, store.ErrNotFound) {
		return "", err
	}

	var fresh []int
	var activity *time.Time
	for _, i := range idx {
		sp := r.Spans[i]
		if existing.EndedAt != nil && sp.Start.After(existing.EndedAt.Add(staleAfter)) {
			continue
		}
		fresh = append(fresh, i)
		activity = latest(activity, ptr(sp.End))
	}
	if stale := len(idx) - len(fresh); stale > 0 {
		j.Log.Warn("join: spans start after their call ended, not joined", "project", r.ProjectID, "count", stale)
	}
	if len(fresh) == 0 {
		return "", nil
	}

	var callID string
	switch {
	case k.roomSID != "":
		callID, err = j.Calls.UpsertCall(ctx, store.CallUpsert{
			OrganisationID: r.OrganisationID, ProjectID: r.ProjectID,
			RoomSID: k.roomSID, JobID: k.jobID, LastActivityAt: activity,
		})
	case existing.ID != "":
		callID, err = existing.ID, j.Calls.TouchCall(ctx, existing.ID, "", nil, activity)
	default:
		callID, err = j.Calls.UpsertCallByJob(ctx, r.OrganisationID, r.ProjectID, k.jobID, activity)
	}
	if err != nil {
		return "", err
	}
	for _, i := range fresh {
		r.Spans[i].CallID = callID
	}
	return callID, nil
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

// spanRef reads a span's own ids in the order the spec sets: room_id, job_id, lk.room_sid,
// lk.job_id. A job id rides along with a room SID so the call learns it.
func spanRef(attrs map[string]string) ref {
	switch {
	case attrs[attrRoomID] != "":
		return ref{roomSID: attrs[attrRoomID], jobID: cmp.Or(attrs[attrJobID], attrs[attrLKJobID])}
	case attrs[attrJobID] != "":
		return ref{jobID: attrs[attrJobID]}
	case attrs[attrLKRoomSID] != "":
		return ref{roomSID: attrs[attrLKRoomSID], jobID: attrs[attrLKJobID]}
	}
	return ref{jobID: attrs[attrLKJobID]}
}

// decode flattens an export into spans. Resource attributes are kept as stored data but never read
// for ids.
func decode(td ptrace.Traces) []store.Span {
	var out []store.Span
	rss := td.ResourceSpans()
	for i := range rss.Len() {
		rs := rss.At(i)
		resource := attrMap(rs.Resource().Attributes())
		sss := rs.ScopeSpans()
		for k := range sss.Len() {
			ss := sss.At(k)
			spans := ss.Spans()
			for n := range spans.Len() {
				sp := spans.At(n)
				attrs := attrMap(sp.Attributes())
				out = append(out, store.Span{
					TraceID:      sp.TraceID().String(),
					SpanID:       sp.SpanID().String(),
					ParentSpanID: sp.ParentSpanID().String(),
					Name:         sp.Name(),
					Kind:         sp.Kind().String(),
					Start:        timestamp(sp.StartTimestamp()),
					End:          timestamp(sp.EndTimestamp()),
					StatusCode:   sp.Status().Code().String(),
					RoomID:       attrs[attrRoomID],
					JobID:        attrs[attrJobID],
					ScopeName:    ss.Scope().Name(),
					Attributes:   attrs,
					Resource:     resource,
				})
			}
		}
	}
	return out
}

func attrMap(m pcommon.Map) map[string]string {
	out := make(map[string]string, m.Len())
	for k, v := range m.All() {
		out[k] = v.AsString()
	}
	return out
}

// timestamp is a span time, zero when the exporter sent none.
func timestamp(t pcommon.Timestamp) time.Time {
	if t == 0 {
		return time.Time{}
	}
	return t.AsTime().UTC()
}
