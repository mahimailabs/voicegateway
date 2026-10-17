package scrape

import (
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/livekit/protocol/livekit"
	"github.com/livekit/protocol/utils"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// projectRoom turns a livekit.Room and its dispatches into the stored projection. Metadata and the
// TURN password are not part of it. dispatches is nil when they were not read.
func projectRoom(r *livekit.Room, dispatches []*livekit.AgentDispatch) store.Room {
	out := store.Room{
		SID:       r.GetSid(),
		Name:      maskRoomName(r.GetName()),
		CreatedAt: secondsOrMillis(r.GetCreationTime(), r.GetCreationTimeMs()),
		Settings: store.RoomSettings{
			// LiveKit reports the room's effective settings, defaults applied, so each value is
			// reported. max_participants 0 means unlimited, as the contract says.
			EmptyTimeout:     ptr(int64(r.GetEmptyTimeout())),
			DepartureTimeout: ptr(int64(r.GetDepartureTimeout())),
			MaxParticipants:  ptr(int64(r.GetMaxParticipants())),
			EgressRecording:  ptr(r.GetActiveRecording()),
		},
	}
	if codecs := r.GetEnabledCodecs(); len(codecs) > 0 {
		mimes := make([]string, 0, len(codecs))
		for _, c := range codecs {
			mimes = append(mimes, c.GetMime())
		}
		out.Settings.Codecs = &mimes
	}
	if dispatches != nil {
		ds := make([]store.Dispatch, 0, len(dispatches))
		for _, d := range dispatches {
			ds = append(ds, projectDispatch(d))
		}
		out.Dispatches = &ds
	}
	return out
}

// projectDispatch turns a livekit.AgentDispatch into the stored projection. Dispatch and job times
// are nanoseconds: the server writes them with time.Now().UnixNano() (docs/api/upstream-sources.md).
func projectDispatch(d *livekit.AgentDispatch) store.Dispatch {
	out := store.Dispatch{
		ID:        d.GetId(),
		AgentName: nonEmpty(d.GetAgentName()),
		CreatedAt: nanos(d.GetState().GetCreatedAt()),
		Jobs:      []store.Job{},
	}
	for _, j := range d.GetState().GetJobs() {
		st := j.GetState()
		job := store.Job{
			ID:        j.GetId(),
			Status:    st.GetStatus().String(),
			StartedAt: nanos(st.GetStartedAt()),
			WorkerID:  nonEmpty(st.GetWorkerId()),
			AgentID:   nonEmpty(st.GetAgentId()),
			Recording: ptr(j.GetEnableRecording()),
			Statuses:  jobStatuses(st),
		}
		out.Jobs = append(out.Jobs, job)
	}
	return out
}

// jobStatuses is what JobState itself says about when the job changed: running from started_at, and
// its final status from ended_at. A job with neither reports only its status, with no time.
func jobStatuses(st *livekit.JobState) []store.StatusAt {
	var out []store.StatusAt
	if at := nanos(st.GetStartedAt()); at != nil {
		out = append(out, store.StatusAt{At: at, Status: livekit.JobStatus_JS_RUNNING.String()})
	}
	if at := nanos(st.GetEndedAt()); at != nil {
		out = append(out, store.StatusAt{At: at, Status: st.GetStatus().String()})
	}
	if len(out) == 0 {
		out = append(out, store.StatusAt{At: nanos(st.GetUpdatedAt()), Status: st.GetStatus().String()})
	}
	return out
}

// projectParticipant turns a livekit.ParticipantInfo into the stored projection, with its state and
// SIP call status. Identity, name, metadata and attributes other than LiveKit's sip.* keys are not
// part of it. agentNames maps an agent participant's identity to the agent its job dispatched.
func projectParticipant(p *livekit.ParticipantInfo, agentNames map[string]string) (store.RoomParticipant, string, string) {
	out := store.RoomParticipant{
		SID:      p.GetSid(),
		Kind:     strings.ToLower(p.GetKind().String()),
		Region:   nonEmpty(p.GetRegion()),
		JoinedAt: secondsOrMillis(p.GetJoinedAt(), p.GetJoinedAtMs()),
	}
	if name, ok := agentNames[p.GetIdentity()]; ok {
		out.AgentName = nonEmpty(name)
	}
	if perm := p.GetPermission(); perm != nil {
		out.Permission = &store.Permission{
			CanPublish:     ptr(perm.GetCanPublish()),
			CanSubscribe:   ptr(perm.GetCanSubscribe()),
			CanPublishData: ptr(perm.GetCanPublishData()),
			Hidden:         ptr(perm.GetHidden()),
			Agent:          ptr(perm.GetAgent()), //nolint:staticcheck // the contract carries LiveKit's own flag
		}
	}
	tracks := make([]store.Track, 0, len(p.GetTracks()))
	var audio int64
	for _, t := range p.GetTracks() {
		if t.GetType() == livekit.TrackType_AUDIO {
			audio++
		}
		tracks = append(tracks, projectTrack(t))
	}
	out.Tracks = &tracks
	out.AudioTracks = &audio

	attrs := p.GetAttributes()
	if p.GetKind() == livekit.ParticipantInfo_SIP {
		out.SIP = &store.SIPLeg{
			TrunkID:     attrs[livekit.AttrSIPTrunkID],
			RuleID:      attrs[livekit.AttrSIPDispatchRuleID],
			InvitedAt:   millisAttr(attrs[livekit.AttrSIPInviteTime]),
			TrunkNumber: maskNumber(attrs[livekit.AttrSIPTrunkNumber]),
			Statuses:    []store.StatusAt{},
		}
	}
	return out, attrs[attrAgentState], attrs[livekit.AttrSIPCallStatus]
}

// attrAgentState is the attribute LiveKit Agents sets on its participant with the agent's state
// (livekit-agents ATTRIBUTE_AGENT_STATE); livekit/protocol has no constant for it.
const attrAgentState = "lk.agent.state"

// phoneDigits matches a run of digits long enough to be a phone number, as LiveKit's individual SIP
// dispatch puts the caller's number in the room name.
var phoneDigits = regexp.MustCompile(`\+?\d{7,}`)

// maskRoomName keeps a room name but masks any phone number in it to its last four digits, the same
// way trunk numbers are masked.
func maskRoomName(name string) string {
	return phoneDigits.ReplaceAllStringFunc(name, maskNumber)
}

// projectTrack turns a livekit.TrackInfo into the stored projection. The deprecated stereo and
// disable_dtx flags give way to audio_features, and the negative disable_red becomes red; video
// tracks have none of the three.
func projectTrack(t *livekit.TrackInfo) store.Track {
	out := store.Track{
		SID:        t.GetSid(),
		Name:       t.GetName(),
		Type:       t.GetType().String(),
		Source:     t.GetSource().String(),
		Mime:       nonEmpty(t.GetMimeType()),
		Muted:      ptr(t.GetMuted()),
		Encryption: t.GetEncryption().String(),
		Features:   []string{},
	}
	if v := t.GetVersion(); v.GetUnixMicro() > 0 {
		at := utils.TimedVersionFromProto(v).Time().UTC()
		out.VersionAt = &at
	}
	for _, f := range t.GetAudioFeatures() {
		out.Features = append(out.Features, f.String())
	}
	if t.GetType() == livekit.TrackType_AUDIO {
		features := t.GetAudioFeatures()
		out.Stereo = ptr(slices.Contains(features, livekit.AudioTrackFeature_TF_STEREO))
		out.DTX = ptr(!slices.Contains(features, livekit.AudioTrackFeature_TF_NO_DTX))
		out.RED = ptr(!t.GetDisableRed())
	}
	return out
}

// secondsOrMillis prefers LiveKit's millisecond field and falls back to its seconds field. Zero in
// both means unreported.
func secondsOrMillis(seconds, millis int64) *time.Time {
	switch {
	case millis > 0:
		t := time.UnixMilli(millis).UTC()
		return &t
	case seconds > 0:
		t := time.Unix(seconds, 0).UTC()
		return &t
	}
	return nil
}

// nanos reads a Unix nanosecond time; zero means unreported.
func nanos(n int64) *time.Time {
	if n <= 0 {
		return nil
	}
	t := time.Unix(0, n).UTC()
	return &t
}

// millisAttr reads an attribute holding Unix milliseconds; empty or invalid means unreported.
func millisAttr(v string) *time.Time {
	n, err := strconv.ParseInt(v, 10, 64)
	if err != nil || n <= 0 {
		return nil
	}
	t := time.UnixMilli(n).UTC()
	return &t
}

// maskNumber keeps only the last four digits of a phone number.
func maskNumber(n string) string {
	digits := make([]rune, 0, len(n))
	for _, r := range n {
		if r >= '0' && r <= '9' {
			digits = append(digits, r)
		}
	}
	if len(digits) == 0 {
		return ""
	}
	if len(digits) > 4 {
		digits = digits[len(digits)-4:]
	}
	return "•••• " + string(digits)
}

func nonEmpty(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

func ptr[T any](v T) *T { return &v }
