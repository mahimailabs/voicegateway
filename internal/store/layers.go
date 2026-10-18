package store

import "time"

// Layers the worker reads from a project's LiveKit server (phase 05).
const (
	LayerMediaServer = "media_server"
	LayerSIP         = "sip"
	LayerEgress      = "egress"
	LayerIngress     = "ingress"
	LayerCloudAgents = "cloud_agents"
	LayerWorkers     = "workers"
)

// Error classes a failed layer read is recorded with. They name the cause the console shows; the
// LiveKit error text itself is never stored.
const (
	ErrorUnauthenticated  = "unauthenticated"
	ErrorPermissionDenied = "permission_denied"
	ErrorUnreachable      = "unreachable"
	ErrorNotSupported     = "not_supported"
	ErrorOther            = "error"
)

// LayerRead is one attempt to read a layer of a project. A failed read carries its error class and
// no samples; it is never stored as an empty result.
type LayerRead struct {
	OrganisationID string
	ProjectID      string
	Layer          string
	ReadAt         time.Time
	OK             bool
	// ErrorClass is empty when OK.
	ErrorClass string
	// Every is the interval the layer is read at, so a reader can tell a gap from a pause.
	Every time.Duration
}

// RoomSample is what one read saw of one room: the redacted projection the console contract
// describes, without what only a series of reads can tell (first and last seen).
type RoomSample struct {
	OrganisationID string
	ProjectID      string
	ReadAt         time.Time
	Room           Room
}

// ParticipantSample is what one read saw of one participant in a room.
type ParticipantSample struct {
	OrganisationID string
	ProjectID      string
	ReadAt         time.Time
	RoomSID        string
	Participant    RoomParticipant
	// State is an agent participant's lk.agent.state attribute (listening, thinking, speaking...),
	// empty for other participants.
	State string
	// SIPCallStatus is the participant's sip.callStatus attribute, empty when it has none.
	SIPCallStatus string
}

// Room is the stored projection of livekit.Room plus its agent dispatches. Pointers are values
// LiveKit may not report; nil stays null, never zero.
type Room struct {
	SID        string        `json:"sid"`
	Name       string        `json:"name"`
	CreatedAt  *time.Time    `json:"createdAt"`
	Settings   RoomSettings  `json:"settings"`
	Dispatches *[]Dispatch   `json:"dispatches"`
	Recording  *RecordingRef `json:"recording"`
}

// RoomSettings are a room's configured limits as LiveKit reports them.
type RoomSettings struct {
	EmptyTimeout     *int64    `json:"emptyTimeout"`
	DepartureTimeout *int64    `json:"departureTimeout"`
	MaxParticipants  *int64    `json:"maxParticipants"`
	Codecs           *[]string `json:"codecs"`
	EgressRecording  *bool     `json:"egressRecording"`
}

// RecordingRef links a room to a recording. The media server read never sets it; recordings come
// from egress (05c) and the agent's session upload.
type RecordingRef struct {
	CallRoom  *string   `json:"callRoom"`
	URL       string    `json:"url"`
	DurationS *float64  `json:"durationS"`
	StartedAt time.Time `json:"startedAt"`
	ID        string    `json:"id"`
}

// Dispatch is the projection of livekit.AgentDispatch.
type Dispatch struct {
	ID        string     `json:"id"`
	AgentName *string    `json:"agentName"`
	CreatedAt *time.Time `json:"createdAt"`
	Jobs      []Job      `json:"jobs"`
}

// Job is the projection of livekit.Job and its JobState.
type Job struct {
	ID        string     `json:"id"`
	Status    string     `json:"status"`
	Statuses  []StatusAt `json:"statuses"`
	StartedAt *time.Time `json:"startedAt"`
	WorkerID  *string    `json:"workerId"`
	AgentID   *string    `json:"agentId"`
	Recording *bool      `json:"recording"`
}

// StatusAt is a status and when it was first reported. At is nil when no time was reported.
type StatusAt struct {
	At     *time.Time `json:"at"`
	Status string     `json:"status"`
}

// RoomParticipant is the projection of livekit.ParticipantInfo. Identity, name, metadata and other
// attributes are not part of it.
type RoomParticipant struct {
	SID         string      `json:"sid"`
	Kind        string      `json:"kind"`
	Region      *string     `json:"region"`
	JoinedAt    *time.Time  `json:"joinedAt"`
	AgentName   *string     `json:"agentName"`
	Permission  *Permission `json:"permission"`
	Tracks      *[]Track    `json:"tracks"`
	AudioTracks *int64      `json:"audioTracks"`
	SIP         *SIPLeg     `json:"sip,omitempty"`
}

// Permission is the projection of livekit.ParticipantPermission.
type Permission struct {
	CanPublish     *bool `json:"canPublish"`
	CanSubscribe   *bool `json:"canSubscribe"`
	CanPublishData *bool `json:"canPublishData"`
	Hidden         *bool `json:"hidden"`
	Agent          *bool `json:"agent"`
}

// Track is the projection of livekit.TrackInfo.
type Track struct {
	SID        string     `json:"sid"`
	Name       string     `json:"name"`
	Type       string     `json:"type"`
	Source     string     `json:"source"`
	Mime       *string    `json:"mime"`
	VersionAt  *time.Time `json:"versionAt"`
	Muted      *bool      `json:"muted"`
	Stereo     *bool      `json:"stereo"`
	DTX        *bool      `json:"dtx"`
	RED        *bool      `json:"red"`
	Encryption string     `json:"encryption"`
	Features   []string   `json:"features"`
}

// SIPLeg is what a SIP participant's sip.* attributes say about its call. Statuses are filled by
// the reader from the series of reads.
type SIPLeg struct {
	TrunkID     string     `json:"trunkId"`
	RuleID      string     `json:"ruleId"`
	InvitedAt   *time.Time `json:"invitedAt"`
	TrunkNumber string     `json:"trunkNumber"`
	Twilio      *bool      `json:"twilio"`
	Statuses    []StatusAt `json:"statuses"`
}

// LayerStatus sums up a layer's reads: when it was last read, when it was last read successfully
// (nil if never) and why the last read failed (empty when it succeeded).
type LayerStatus struct {
	LastReadAt     *time.Time
	LastGoodReadAt *time.Time
	ErrorClass     string
}

// RoomSeen is a room as the latest read saw it, with the first and last read that saw it.
type RoomSeen struct {
	Room      Room
	FirstSeen time.Time
	LastSeen  time.Time
}

// ParticipantSeen is a participant as the latest read saw it, with the first and last read that
// saw it.
type ParticipantSeen struct {
	RoomSID     string
	Participant RoomParticipant
	FirstSeen   time.Time
	LastSeen    time.Time
}

// ParticipantChange is a read where a participant's agent state or SIP call status changed, or its
// first read.
type ParticipantChange struct {
	RoomSID        string
	ParticipantSID string
	State          string
	SIPCallStatus  string
	At             time.Time
}
