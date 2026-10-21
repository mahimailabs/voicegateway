package join

import (
	"context"
	"encoding/json"
	"errors"
	"maps"
	"time"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// sdkBatch joins an SDK batch by its room SID, else its job id, like a session report (0012). An
// SDK runs inside the job, so a batch whose job opened no call yet opens one by job id, as a span
// does; a later room event joins it by room SID. Its events' times are when the call was active. The
// batch's custom fields are merged onto the call, later values winning.
func (j *Joiner) sdkBatch(ctx context.Context, r *store.Event) error {
	var b event.SDKBatch
	if err := json.Unmarshal(r.Payload, &b); err != nil || len(b.Events) == 0 {
		j.unmatched(r, reasonUndecodable)
		return nil
	}
	first, last := b.Events[0].At, b.Events[0].At
	fields := map[string]string{}
	for _, e := range b.Events {
		if e.At.Before(first) {
			first = e.At
		}
		if e.At.After(last) {
			last = e.At
		}
		if e.Type == event.SDKFields {
			var d event.SDKFieldsData
			if json.Unmarshal(e.Data, &d) == nil {
				maps.Copy(fields, d.Fields)
			}
		}
	}
	id, err := j.joinSDK(ctx, r, b.Call, &first, &last)
	if err != nil || id == "" {
		return err
	}
	r.CallID = id
	if len(fields) == 0 {
		return nil
	}
	dropped, err := j.Calls.MergeCallFields(ctx, id, fields)
	if dropped > 0 {
		// The batch is stored; only the keys past the call's cap are not.
		j.Log.Warn("join: call fields over their cap, keys dropped", "project", r.ProjectID, "call", id, "dropped", dropped, "cap", store.MaxCallFields)
	}
	return err
}

// joinSDK returns the call of a batch's room SID or job id, opening it when none exists yet.
func (j *Joiner) joinSDK(ctx context.Context, r *store.Event, c event.SDKCall, first, last *time.Time) (string, error) {
	roomSID, roomName, jobID := deref(c.RoomSID), deref(c.RoomName), deref(c.JobID)
	switch {
	case roomSID != "":
		return j.Calls.UpsertCall(ctx, store.CallUpsert{
			OrganisationID: r.OrganisationID, ProjectID: r.ProjectID,
			RoomSID: roomSID, RoomName: roomName, JobID: jobID,
			LastActivityAt: last, FirstSeenAt: first,
		})
	case jobID != "":
		known, err := j.Calls.CallByJob(ctx, r.OrganisationID, r.ProjectID, jobID)
		if errors.Is(err, store.ErrNotFound) {
			return j.Calls.UpsertCallByJob(ctx, r.OrganisationID, r.ProjectID, jobID, last, first)
		}
		if err != nil {
			return "", err
		}
		return known.ID, j.Calls.TouchCall(ctx, known.ID, "", nil, last, first)
	}
	j.unmatched(r, reasonNoRoom)
	return "", nil
}

func deref(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}
