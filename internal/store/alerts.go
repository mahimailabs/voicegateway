package store

import (
	"encoding/json"
	"errors"
	"time"
)

// Alert rules, as alerts-model.ts lists them (decision 0020, docs/specs/alerts-behaviour.md).
var AlertRuleIDs = []string{"slow-answer", "cut-off", "job-failed", "slow-join", "plain-phone", "recording-lost", "ingress-error"}

// AlertChannelKinds are the channels a rule may send to (docs/specs/alert-channels.md).
var AlertChannelKinds = []string{"email", "webhook", "slack", "teams", "discord", "pagerduty", "opsgenie"}

var (
	// ErrAlertIncidentState answers a resolve of an incident that is not firing, or whose rule a
	// person may not resolve.
	ErrAlertIncidentState = errors.New("alert incident cannot be resolved by a person in its state")
	// ErrAlertChannelMissing answers a test of a channel that is not configured.
	ErrAlertChannelMissing = errors.New("alert channel not configured")
)

// AlertOccurrence is one thing a rule matched, as the Issues page stored it. Subject groups
// occurrences into incidents within the project: the agent for call and job rules (empty when
// unknown), else the component.
type AlertOccurrence struct {
	OrganisationID, ProjectID, RuleID, OccurrenceID, Subject string
	At                                                       *time.Time
	Detail                                                   AlertDetail
}

// AlertDetail is an occurrence as the console shows it.
type AlertDetail struct {
	Where  string  `json:"where"`
	Detail string  `json:"detail"`
	CallID *string `json:"callId"`
}

// AlertIncident groups occurrences of one rule on one subject.
type AlertIncident struct {
	ID, OrganisationID, ProjectID, RuleID, Subject, SubjectLabel, State string
	OpenedAt                                                            time.Time
	ResolvedAt                                                          *time.Time
	// ResolvedBy is "condition" when the resolve rule held, else the user who resolved it.
	ResolvedBy          *string
	Occurrences         int
	NotifiedOccurrences int
	NotifiedAt          *time.Time
	// SinceLastMessage is set on the change being sent: occurrences since the previous message.
	SinceLastMessage int
	Changes          int
	LastOccurrenceAt *time.Time
	Last             AlertDetail
	HoldReason       *string
	SilentSince      *time.Time
}

// AlertRule is a rule's saved setup. No saved row reads as on, sending nowhere.
type AlertRule struct {
	RuleID string   `json:"ruleId"`
	On     bool     `json:"on"`
	To     []string `json:"to"`
}

// AlertChannel is a configured channel as the console may see it: never its URL, secret or
// recipients' credentials.
type AlertChannel struct {
	Kind      string
	Target    string
	Broken    bool
	UpdatedAt time.Time
	Last      *AlertDeliveryStatus
}

// AlertDeliveryStatus is one delivery's state, for Settings and the Alerts history.
type AlertDeliveryStatus struct {
	ID            string
	IncidentID    *string
	Channel       string
	Change        string
	State         string
	Attempts      int
	NextAttemptAt *time.Time
	LastStatus    *string
	LastError     *string
	DeliveredAt   *time.Time
	CreatedAt     time.Time
}

// AlertDelivery is a due delivery the worker claimed.
type AlertDelivery struct {
	ID, OrganisationID, ProjectID string
	IncidentID                    *string
	Channel, Change, Key          string
	Message                       json.RawMessage
	Attempts                      int
	CreatedAt                     time.Time
}

// AlertAttempt is the outcome of sending one delivery once. State is delivered, retrying or failed.
type AlertAttempt struct {
	At            time.Time
	State         string
	Status        string
	Error         *string
	NextAttemptAt time.Time
	// Broken marks the channel broken (a non-retryable refusal) or, on delivered, clears it.
	Broken bool
	// Issue lists a failed delivery on the Issues page, with this text.
	Issue string
}

// AlertCompose builds the message of one incident change for one channel kind.
type AlertCompose func(inc AlertIncident, change, channel string) (json.RawMessage, error)

// AlertCallWindow counts an agent's evaluated calls since a time.
type AlertCallWindow struct {
	Calls, Matched int
	LastCallAt     *time.Time
}

// AlertJobWindow counts an agent's jobs dispatched after a time.
type AlertJobWindow struct {
	Succeeded, JoinedInTime int
	LastSeenAt              *time.Time
}

// AlertSource is when a layer's reads were last good, and until when the latest read counts.
type AlertSource struct {
	GoodAt     *time.Time
	FreshUntil *time.Time
}

// StandingAlert is a standing condition true now, such as a plain-RTP trunk.
type StandingAlert struct {
	OrganisationID, ProjectID, SourceID string
	// ChangedAt is when the condition last turned on or changed, not when a read last saw it.
	ChangedAt     time.Time
	Where, Detail string
}

// AlertEvent is what one rule matched in a range, for the Alerts page chart.
type AlertEvent struct {
	RuleID string
	AlertDetail
	At *time.Time
}

// AlertWebhookConfig is a webhook channel's sealed configuration.
type AlertWebhookConfig struct {
	URL string `json:"url"`
	// Secret signs every delivery (Standard Webhooks, whsec_ prefix).
	Secret string `json:"secret"`
}

// AlertEmailConfig is an email channel's sealed configuration.
type AlertEmailConfig struct {
	Recipients []string `json:"recipients"`
}

// AlertURLConfig is a Slack, Microsoft Teams or Discord channel's sealed configuration: the incoming
// webhook URL, whose path is the credential.
type AlertURLConfig struct {
	URL string `json:"url"`
}

// AlertKeyConfig is a PagerDuty or Opsgenie channel's sealed configuration: the PagerDuty routing
// key or the Opsgenie API integration key, and the provider's region, us or eu.
type AlertKeyConfig struct {
	Key    string `json:"key"`
	Region string `json:"region"`
}
