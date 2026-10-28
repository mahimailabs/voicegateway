package api

import (
	"errors"
	"fmt"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Watch limits of the media server read: the rooms seen in the last day, newest first.
const (
	mediaServerSince = 24 * time.Hour
	mediaServerRooms = 500
)

// staleAfter is how many intervals may pass without a read before a gap ends a watch window.
const staleAfter = 3

type mediaServer struct {
	Source       string              `json:"source"`
	Server       *struct{}           `json:"server"`
	Rooms        []mediaRoom         `json:"rooms"`
	Windows      []window            `json:"windows"`
	SIPTrunks    map[string]struct{} `json:"sipTrunks"`
	SourceStatus sourceStatus        `json:"sourceStatus"`
}

type mediaRoom struct {
	store.Room
	FirstSeenAt  *time.Time         `json:"firstSeenAt"`
	LastSeenAt   *time.Time         `json:"lastSeenAt"`
	Participants []mediaParticipant `json:"participants"`
}

type mediaParticipant struct {
	store.RoomParticipant
	FirstSeenAt *time.Time `json:"firstSeenAt"`
	LastSeenAt  *time.Time `json:"lastSeenAt"`
	States      []stateAt  `json:"states"`
}

type stateAt struct {
	At    *time.Time `json:"at"`
	State string     `json:"state"`
}

type window struct {
	Start        *time.Time `json:"start"`
	End          *time.Time `json:"end"`
	EverySeconds *int64     `json:"everySeconds"`
	Detail       *bool      `json:"detail"`
}

// sourceStatus says how fresh a sampled read is: when the layer was last read, when it was last
// read successfully, and why the last read failed.
type sourceStatus struct {
	LastReadAt     *time.Time `json:"lastReadAt"`
	LastGoodReadAt *time.Time `json:"lastGoodReadAt"`
	ErrorClass     *string    `json:"errorClass"`
}

const mediaServerSource = "Sampled by the worker from the project's LiveKit server: RoomService ListRooms, " +
	"with ListParticipants and AgentDispatchService ListDispatch for new or changed rooms. ServerInfo needs " +
	"an RTC join and is not read. SIP trunks are served by the SIP read."

func (a *API) mediaServer(w http.ResponseWriter, r *http.Request) {
	// Live snapshots must not be replayed from an HTTP cache.
	w.Header().Set("Cache-Control", "no-store")
	ctx := r.Context()
	org, proj := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
	if _, err := a.Projects.Project(ctx, org, proj); err != nil {
		if errors.Is(err, store.ErrNotFound) {
			writeError(w, r, http.StatusNotFound, codeNotFound, "no such project")
			return
		}
		a.internal(w, r, "find project", err)
		return
	}
	st, err := a.Layers.LayerStatus(ctx, org, proj, store.LayerMediaServer)
	if err != nil {
		a.internal(w, r, "media server status", err)
		return
	}
	if st.LastGoodReadAt == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, unavailableMessage(st))
		return
	}
	since := a.Now().Add(-mediaServerSince)
	reads, err := a.Layers.LayerReads(ctx, org, proj, store.LayerMediaServer, since)
	if err != nil {
		a.internal(w, r, "media server reads", err)
		return
	}
	rooms, err := a.Layers.RoomsSince(ctx, org, proj, since, mediaServerRooms)
	if err != nil {
		a.internal(w, r, "media server rooms", err)
		return
	}
	sids := make([]string, len(rooms))
	for i, rm := range rooms {
		sids[i] = rm.Room.SID
	}
	parts, err := a.Layers.Participants(ctx, org, proj, sids)
	if err != nil {
		a.internal(w, r, "media server participants", err)
		return
	}
	changes, err := a.Layers.ParticipantChanges(ctx, org, proj, sids)
	if err != nil {
		a.internal(w, r, "media server participant changes", err)
		return
	}
	writeJSON(w, http.StatusOK, mediaServer{
		Source:       mediaServerSource,
		Rooms:        joinRooms(rooms, parts, changes),
		Windows:      windows(reads),
		SIPTrunks:    map[string]struct{}{},
		SourceStatus: statusOf(st),
	})
}

// joinRooms puts each participant, with its agent state and SIP call status histories, under its
// room. changes come oldest first; a value equal to the one before is not a change.
func joinRooms(rooms []store.RoomSeen, parts []store.ParticipantSeen, changes []store.ParticipantChange) []mediaRoom {
	type pkey struct{ room, participant string }
	states := map[pkey][]stateAt{}
	sip := map[pkey][]store.StatusAt{}
	for _, c := range changes {
		k := pkey{c.RoomSID, c.ParticipantSID}
		at := c.At
		if s := states[k]; c.State != "" && (len(s) == 0 || s[len(s)-1].State != c.State) {
			states[k] = append(s, stateAt{At: &at, State: c.State})
		}
		if s := sip[k]; c.SIPCallStatus != "" && (len(s) == 0 || s[len(s)-1].Status != c.SIPCallStatus) {
			sip[k] = append(s, store.StatusAt{At: &at, Status: c.SIPCallStatus})
		}
	}
	byRoom := map[string][]mediaParticipant{}
	for _, p := range parts {
		k := pkey{p.RoomSID, p.Participant.SID}
		mp := mediaParticipant{RoomParticipant: p.Participant, FirstSeenAt: timePtr(p.FirstSeen), LastSeenAt: timePtr(p.LastSeen), States: states[k]}
		if mp.States == nil {
			mp.States = []stateAt{}
		}
		if mp.SIP != nil {
			leg := *mp.SIP
			leg.Statuses = sip[k]
			if leg.Statuses == nil {
				leg.Statuses = []store.StatusAt{}
			}
			mp.SIP = &leg
		}
		byRoom[p.RoomSID] = append(byRoom[p.RoomSID], mp)
	}
	out := make([]mediaRoom, 0, len(rooms))
	for _, rm := range rooms {
		mr := mediaRoom{Room: rm.Room, FirstSeenAt: timePtr(rm.FirstSeen), LastSeenAt: timePtr(rm.LastSeen), Participants: byRoom[rm.Room.SID]}
		if mr.Participants == nil {
			mr.Participants = []mediaParticipant{}
		}
		out = append(out, mr)
	}
	return out
}

// windows are the spans of continuous good reads: a failed read, or a gap longer than staleAfter
// intervals, ends one.
func windows(reads []store.LayerRead) []window {
	out := []window{}
	var start, end time.Time
	var every time.Duration
	flush := func() {
		if start.IsZero() {
			return
		}
		s, e := start, end
		secs := int64(every / time.Second)
		detail := true
		out = append(out, window{Start: &s, End: &e, EverySeconds: &secs, Detail: &detail})
		start = time.Time{}
	}
	for _, rd := range reads {
		if !rd.OK || (!start.IsZero() && (rd.Every != every || rd.ReadAt.Sub(end) > staleAfter*every)) {
			flush()
		}
		if !rd.OK {
			continue
		}
		if start.IsZero() {
			start, every = rd.ReadAt, rd.Every
		}
		end = rd.ReadAt
	}
	flush()
	return out
}

func statusOf(st store.LayerStatus) sourceStatus {
	out := sourceStatus{LastReadAt: st.LastReadAt, LastGoodReadAt: st.LastGoodReadAt}
	if st.ErrorClass != "" {
		c := st.ErrorClass
		out.ErrorClass = &c
	}
	return out
}

// unavailableMessage names why a layer never read successfully has nothing to serve.
func unavailableMessage(st store.LayerStatus) string {
	if st.LastReadAt == nil {
		return "the LiveKit server has not been read yet"
	}
	switch st.ErrorClass {
	case store.ErrorUnauthenticated:
		return "LiveKit rejected the project's API key or secret"
	case store.ErrorPermissionDenied:
		return "the project's API key lacks the grant this read needs"
	case store.ErrorUnreachable:
		return "the LiveKit server could not be reached"
	case store.ErrorNotSupported:
		return "the LiveKit server does not support this read"
	default:
		return fmt.Sprintf("reading the LiveKit server failed (%s)", st.ErrorClass)
	}
}

func (a *API) internal(w http.ResponseWriter, r *http.Request, what string, err error) {
	a.Log.Error("api: "+what, "err", err)
	writeError(w, r, http.StatusInternalServerError, codeInternal, "the read failed")
}

func timePtr(t time.Time) *time.Time {
	if t.IsZero() {
		return nil
	}
	return &t
}
