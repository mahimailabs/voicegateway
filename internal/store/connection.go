package store

import "time"

// Connection sources: LiveKit's webhooks and an agent's observability uploads.
const (
	SourceWebhook = "webhook"
	SourceAgent   = "agent"
)

// Reason codes for a refused ingestion request (docs/specs/first-call-setup.md). The console shows a
// fix for each.
const (
	ReasonTokenMissing     = "token_missing"
	ReasonKeyMismatch      = "key_mismatch"
	ReasonSignatureInvalid = "signature_invalid"
	ReasonGrantMissing     = "grant_missing"
	ReasonBodyChanged      = "body_changed"
	ReasonPlanInactive     = "plan_inactive"
	ReasonPlanCap          = "plan_cap"
	ReasonTooLarge         = "too_large"
)

// Connection is how one source of a project's ingestion last went. A nil field was never seen.
type Connection struct {
	Source         string
	LastAcceptedAt *time.Time
	LastRejectedAt *time.Time
	Reason         *string
	// UsedKey is the LiveKit API key a refused token named, kept only for key_mismatch.
	UsedKey *string
}

// FirstCall is the earliest call of a project in the call index. StartedAt is nil when no source
// reported when it started.
type FirstCall struct {
	ID        string
	StartedAt *time.Time
}
