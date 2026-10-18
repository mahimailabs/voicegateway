package store

import "time"

// EgressSample is what one read saw of one egress: the redacted projection the console contract
// describes, without what only a series of reads can tell (history, first and last seen).
type EgressSample struct {
	OrganisationID string
	ProjectID      string
	ReadAt         time.Time
	// UpdatedAt is LiveKit's own time of the egress's last change (EgressInfo.updated_at), nil when
	// it reported none.
	UpdatedAt *time.Time
	Egress    Egress
}

// Egress is the stored projection of livekit.EgressInfo. Upload credentials, stream keys, webhook
// configs and the request's own URLs are never part of it; stream outputs keep their scheme and host
// only, and phone numbers in names keep their last 4 digits. Pointers are values LiveKit may not
// report; nil stays null, never zero.
type Egress struct {
	ID        string         `json:"id"`
	Room      string         `json:"room"`
	Status    string         `json:"status"`
	StartedAt *time.Time     `json:"startedAt"`
	EndedAt   *time.Time     `json:"endedAt"`
	Error     *string        `json:"error"`
	Details   string         `json:"details"`
	Request   EgressRequest  `json:"request"`
	File      *EgressFile    `json:"file"`
	Streams   []EgressStream `json:"streams"`
}

// EgressRequest is the shape of the request that started an egress: what it records and where it
// writes, never the storage credentials or the stream URLs. FileType and Storage are empty when the
// request names no file output or no storage.
type EgressRequest struct {
	Kind      string   `json:"kind"`
	AudioOnly *bool    `json:"audioOnly"`
	FileType  string   `json:"fileType"`
	Storage   string   `json:"storage"`
	Outputs   []string `json:"outputs"`
}

// EgressFile is the projection of livekit.FileInfo.
type EgressFile struct {
	Name       string   `json:"name"`
	SizeBytes  *int64   `json:"sizeBytes"`
	DurationS  *float64 `json:"durationS"`
	BucketHost *string  `json:"bucketHost"`
	Playable   *string  `json:"playable"`
}

// EgressStream is the projection of livekit.StreamInfo: one stream output, by its scheme and host.
type EgressStream struct {
	Host      *string    `json:"host"`
	Status    string     `json:"status"`
	Error     *string    `json:"error"`
	StartedAt *time.Time `json:"startedAt"`
	EndedAt   *time.Time `json:"endedAt"`
}

// IngressSample is what one read saw of one ingress endpoint.
type IngressSample struct {
	OrganisationID string
	ProjectID      string
	ReadAt         time.Time
	// UpdatedAt is LiveKit's own time of the endpoint's last change (IngressState.updated_at), nil
	// when it reported none.
	UpdatedAt *time.Time
	Ingress   Ingress
}

// Ingress is the stored projection of livekit.IngressInfo. The stream key, credentials and query
// values in its URL, the participant's name and metadata are never part of it.
type Ingress struct {
	ID          string         `json:"id"`
	Name        string         `json:"name"`
	InputType   string         `json:"inputType"`
	URL         string         `json:"url"`
	Room        string         `json:"room"`
	Participant string         `json:"participant"`
	Transcoding *bool          `json:"transcoding"`
	Reusable    *bool          `json:"reusable"`
	Enabled     *bool          `json:"enabled"`
	Status      string         `json:"status"`
	Error       *string        `json:"error"`
	StartedAt   *time.Time     `json:"startedAt"`
	EndedAt     *time.Time     `json:"endedAt"`
	AudioIn     *IngressAudio  `json:"audioIn"`
	VideoIn     *IngressVideo  `json:"videoIn"`
	Published   []IngressTrack `json:"published"`
}

// IngressAudio is the projection of livekit.InputAudioState.
type IngressAudio struct {
	MimeType       string `json:"mimeType"`
	AverageBitrate *int64 `json:"averageBitrate"`
	Channels       *int64 `json:"channels"`
	SampleRate     *int64 `json:"sampleRate"`
}

// IngressVideo is the projection of livekit.InputVideoState.
type IngressVideo struct {
	MimeType       *string  `json:"mimeType"`
	AverageBitrate *float64 `json:"averageBitrate"`
	Width          *int64   `json:"width"`
	Height         *int64   `json:"height"`
	Framerate      *float64 `json:"framerate"`
}

// IngressTrack is a track an ingress publishes, from livekit.TrackInfo.
type IngressTrack struct {
	Type     string `json:"type"`
	MimeType string `json:"mimeType"`
	Source   string `json:"source"`
}

// EgressSeen is an egress as the latest read saw it, with the first and last read that saw it.
type EgressSeen struct {
	Egress    Egress
	FirstSeen time.Time
	LastSeen  time.Time
}

// IngressSeen is an ingress endpoint as the latest read saw it, with the first and last read that
// saw it.
type IngressSeen struct {
	Ingress   Ingress
	FirstSeen time.Time
	LastSeen  time.Time
}

// StatusChange is a read where an egress or ingress status differed from the read before, or its
// first read. At is when the worker read it; UpdatedAt is LiveKit's own time of the change, nil when
// it reported none.
type StatusChange struct {
	ID        string
	Status    string
	At        time.Time
	UpdatedAt *time.Time
}

// WebhookStatus is the status a stored egress or ingress webhook reported. At is the time LiveKit
// gave, nil when it gave none; ReceivedAt orders it then.
type WebhookStatus struct {
	ID         string
	Status     string
	At         *time.Time
	ReceivedAt time.Time
}
