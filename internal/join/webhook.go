package join

import (
	"context"
	"errors"
	"time"

	"github.com/livekit/protocol/livekit"
	"google.golang.org/protobuf/encoding/protojson"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// webhook joins a LiveKit webhook: by its room SID (the room's, an Egress's room_id or a job's
// room), else, for Ingress, by room name at its reported time.
func (j *Joiner) webhook(ctx context.Context, r *store.Event) error {
	var ev livekit.WebhookEvent
	if err := (protojson.UnmarshalOptions{DiscardUnknown: true}).Unmarshal(r.Payload, &ev); err != nil {
		j.unmatched(r, reasonUndecodable)
		return nil
	}
	at := ptr(seconds(ev.GetCreatedAt()))
	room := ev.GetRoom()
	sid, name := room.GetSid(), room.GetName()
	if sid == "" {
		sid = ev.GetEgressInfo().GetRoomId()
	}
	if sid == "" {
		sid, name = ev.GetJob().GetRoom().GetSid(), ev.GetJob().GetRoom().GetName()
	}

	if sid == "" {
		roomName := ev.GetIngressInfo().GetRoomName()
		if roomName == "" || at == nil {
			j.unmatched(r, reasonNoRoom)
			return nil
		}
		id, err := j.Calls.CallInRoom(ctx, r.OrganisationID, r.ProjectID, roomName, *at)
		if errors.Is(err, store.ErrNotFound) {
			j.unmatched(r, reasonNoCall)
			return nil
		}
		if err != nil {
			return err
		}
		r.CallID = id
		return j.Calls.TouchCall(ctx, id, "", nil, at)
	}

	id, err := j.Calls.UpsertCall(ctx, store.CallUpsert{
		OrganisationID: r.OrganisationID,
		ProjectID:      r.ProjectID,
		RoomSID:        sid,
		RoomName:       name,
		JobID:          ev.GetJob().GetId(),
		StartedAt:      ptr(millisOrSeconds(room.GetCreationTimeMs(), room.GetCreationTime())),
		LastActivityAt: at,
	})
	if err != nil {
		return err
	}
	r.CallID = id

	if p := ev.GetParticipant(); p.GetSid() != "" {
		if err := j.participant(ctx, r, id, ev.GetEvent(), p, at); err != nil {
			return err
		}
	}
	if ev.GetEvent() == "room_finished" {
		return j.Calls.FinishCall(ctx, id, at)
	}
	return nil
}

// participantKinds are the ParticipantInfo.Kind values in livekit/protocol v1.52.1, as stored.
var participantKinds = map[livekit.ParticipantInfo_Kind]string{
	livekit.ParticipantInfo_STANDARD:  "standard",
	livekit.ParticipantInfo_INGRESS:   "ingress",
	livekit.ParticipantInfo_EGRESS:    "egress",
	livekit.ParticipantInfo_SIP:       "sip",
	livekit.ParticipantInfo_AGENT:     "agent",
	livekit.ParticipantInfo_CONNECTOR: "connector",
	livekit.ParticipantInfo_BRIDGE:    "bridge",
}

// participant stores the webhook's participant. It joined when LiveKit says; it left when a
// participant_left or participant_connection_aborted webhook was created.
func (j *Joiner) participant(ctx context.Context, r *store.Event, callID, name string, p *livekit.ParticipantInfo, at *time.Time) error {
	kind, ok := participantKinds[p.GetKind()]
	if !ok {
		// A kind newer than the protocol version in use: the participant is not stored rather than
		// stored under a kind it is not.
		j.Log.Warn("join: participant kind unknown, not stored", "project", r.ProjectID, "kind", int32(p.GetKind()))
		return nil
	}
	var left *time.Time
	if name == "participant_left" || name == "participant_connection_aborted" {
		left = at
	}
	return j.Calls.UpsertParticipant(ctx, store.Participant{
		CallID:   callID,
		SID:      p.GetSid(),
		Identity: p.GetIdentity(),
		Kind:     kind,
		JoinedAt: ptr(millisOrSeconds(p.GetJoinedAtMs(), p.GetJoinedAt())),
		LeftAt:   left,
	})
}

func seconds(s int64) time.Time {
	if s <= 0 {
		return time.Time{}
	}
	return time.Unix(s, 0).UTC()
}

// millisOrSeconds prefers LiveKit's millisecond field and falls back to its seconds one.
func millisOrSeconds(ms, s int64) time.Time {
	if ms > 0 {
		return time.UnixMilli(ms).UTC()
	}
	return seconds(s)
}
