package event

import (
	"errors"
	"time"
)

// Kind names what produced the payload, so the worker knows how to decode it.
type Kind string

// KindLiveKitWebhook is a LiveKit webhook: the verified request body, protojson WebhookEvent.
const KindLiveKitWebhook Kind = "livekit.webhook"

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
