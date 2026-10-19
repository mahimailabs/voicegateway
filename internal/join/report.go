package join

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"time"

	"github.com/livekit/protocol/livekit"
	"google.golang.org/protobuf/encoding/protojson"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// maxDurationMillis is the longest duration, in milliseconds, a time.Duration holds.
const maxDurationMillis = uint64(math.MaxInt64 / int64(time.Millisecond))

// sessionReport joins a session report by its header's room SID, else its job id. The start it
// reports is the room's start; its activity is its own start plus its duration. A recording web
// stored is linked to the call it joins; one that joins no call stays in the bucket until it
// expires.
func (j *Joiner) sessionReport(ctx context.Context, r *store.Event) error {
	report, h, ok := j.decodeReport(r)
	if !ok {
		return nil
	}
	if err := j.joinReport(ctx, r, h); err != nil || r.CallID == "" || report.Recording == nil {
		return err
	}
	link := store.RecordingLink{
		CallID: r.CallID, ObjectKey: report.Recording.Key, SizeBytes: report.Recording.SizeBytes,
		StoredAt: report.Recording.StoredAt,
	}
	// The header's start is the recording's wall-clock start; LiveKit Agents sends the epoch when it
	// has none, which is no time anyone reported.
	if t := h.GetStartTime(); t != nil && t.AsTime().Unix() > 0 {
		link.StartedAt = ptr(t.AsTime())
	}
	return j.Calls.LinkRecording(ctx, link)
}

// decodeReport decodes a session report and its header; an undecodable one is unmatched.
func (j *Joiner) decodeReport(r *store.Event) (event.SessionReport, *livekit.MetricsRecordingHeader, bool) {
	var report event.SessionReport
	h := &livekit.MetricsRecordingHeader{}
	if err := json.Unmarshal(r.Payload, &report); err != nil {
		j.unmatched(r, reasonUndecodable)
		return report, nil, false
	}
	if err := (protojson.UnmarshalOptions{DiscardUnknown: true}).Unmarshal(report.Header, h); err != nil {
		j.unmatched(r, reasonUndecodable)
		return report, nil, false
	}
	return report, h, true
}

// joinReport sets r.CallID from the header, or marks r unmatched.
func (j *Joiner) joinReport(ctx context.Context, r *store.Event, h *livekit.MetricsRecordingHeader) error {
	var started, activity, reportStart *time.Time
	if h.GetRoomStartTime() != nil {
		started = ptr(h.GetRoomStartTime().AsTime())
	}
	if h.GetStartTime() != nil {
		reportStart = ptr(h.GetStartTime().AsTime())
		activity = reportStart
		// A duration too long for time.Duration is no time anyone reported; the start stands.
		if ms := h.GetDuration(); ms <= maxDurationMillis {
			activity = ptr(h.GetStartTime().AsTime().Add(time.Duration(ms) * time.Millisecond)) //nolint:gosec // bounded above
		}
	}

	switch {
	case h.GetRoomId() != "":
		id, err := j.Calls.UpsertCall(ctx, store.CallUpsert{
			OrganisationID: r.OrganisationID, ProjectID: r.ProjectID,
			RoomSID: h.GetRoomId(), RoomName: h.GetRoomName(), JobID: h.GetJobId(),
			StartedAt: started, LastActivityAt: activity, FirstSeenAt: earliest(started, reportStart),
		})
		if err != nil {
			return err
		}
		r.CallID = id
		return nil
	case h.GetJobId() != "":
		c, err := j.Calls.CallByJob(ctx, r.OrganisationID, r.ProjectID, h.GetJobId())
		if errors.Is(err, store.ErrNotFound) {
			j.unmatched(r, reasonNoCall)
			return nil
		}
		if err != nil {
			return err
		}
		r.CallID = c.ID
		return j.Calls.TouchCall(ctx, c.ID, "", started, activity, earliest(started, reportStart))
	}
	j.unmatched(r, reasonNoRoom)
	return nil
}
