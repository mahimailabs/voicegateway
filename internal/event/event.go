package event

import (
	"encoding/json"
	"errors"
	"time"
)

// Kind names what produced the payload, so the worker knows how to decode it.
type Kind string

// The kinds web appends, and what each Payload holds.
const (
	// KindLiveKitWebhook is a LiveKit webhook: the verified request body, protojson WebhookEvent.
	KindLiveKitWebhook Kind = "livekit.webhook"
	// KindAgentTraces, KindAgentLogs and KindAgentMetrics are a LiveKit agent's OTLP export
	// (decision 0014): the request decompressed, an OTLP Export*ServiceRequest in protobuf.
	KindAgentTraces  Kind = "livekit.agent.traces"
	KindAgentLogs    Kind = "livekit.agent.logs"
	KindAgentMetrics Kind = "livekit.agent.metrics"
	// KindAgentSessionReport is a LiveKit agent's session upload (decisions 0014, 0015): a
	// SessionReport encoded as JSON.
	KindAgentSessionReport Kind = "livekit.agent.session_report"
)

// SessionReport is the Payload of KindAgentSessionReport.
type SessionReport struct {
	// Header is the upload's MetricsRecordingHeader as protojson: room, job and start time.
	Header json.RawMessage `json:"header"`
	// ChatHistory is chat_history.json exactly as uploaded; absent when the agent sent none.
	ChatHistory json.RawMessage `json:"chat_history,omitempty"`
	// AudioDiscarded is true when the upload carried recording.ogg and it was not kept, because
	// the project has no recordings bucket (0015). The audio itself never enters the stream.
	AudioDiscarded bool `json:"audio_discarded"`
}

// Envelope is one ingested record, attributed to the project whose LiveKit key signed it.
type Envelope struct {
	// ID is the source's own event id, the dedupe key for webhooks (decision 0012). Empty when the
	// source sent none: unknown, never invented.
	ID             string
	Kind           Kind
	OrganisationID string
	ProjectID      string
	ReceivedAt     time.Time
	// Payload is the body exactly as verified. It is never logged.
	Payload []byte
}

// Validate reports the first field an envelope cannot travel without.
func (e Envelope) Validate() error {
	switch {
	case e.Kind == "":
		return errors.New("envelope has no kind")
	case e.OrganisationID == "" || e.ProjectID == "":
		return errors.New("envelope has no project")
	case e.ReceivedAt.IsZero():
		return errors.New("envelope has no received time")
	}
	return nil
}
