package alert

import (
	"context"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Store is what the evaluator and the deliverer keep in Postgres (pgstore.Store).
type Store interface {
	TryAlertLock(ctx context.Context) (release func(), ok bool, err error)
	NewAlertOccurrences(ctx context.Context, since time.Time, limit int) ([]store.AlertOccurrence, error)
	StandingAlerts(ctx context.Context) ([]store.StandingAlert, error)
	ApplyAlertOccurrence(ctx context.Context, occ store.AlertOccurrence, label string, now, flapSince time.Time, compose store.AlertCompose) (store.AlertIncident, string, error)
	FiringAlertIncidents(ctx context.Context, limit int) ([]store.AlertIncident, error)
	SendingAlertRules(ctx context.Context) (map[string]bool, error)
	NotifyAlertIncident(ctx context.Context, incidentID, change string, now time.Time, compose store.AlertCompose) (int, error)
	ResolveAlertIncident(ctx context.Context, incidentID string, now time.Time, compose store.AlertCompose) (bool, error)
	HoldAlertIncident(ctx context.Context, incidentID string, reason *string, silentSince *time.Time) error
	AlertCallWindow(ctx context.Context, organisationID, projectID, issueID, agent string, since time.Time) (store.AlertCallWindow, error)
	AlertJobWindow(ctx context.Context, organisationID, projectID, agent string, after time.Time) (store.AlertJobWindow, error)
	AlertSource(ctx context.Context, organisationID, projectID, layer string) (store.AlertSource, error)
	PruneAlerts(ctx context.Context, historyBefore, seenBefore time.Time) error
}

// Deliveries is what the deliverer reads and records.
type Deliveries interface {
	ClaimAlertDeliveries(ctx context.Context, now, leaseUntil time.Time, limit int) ([]store.AlertDelivery, error)
	AlertChannelConfig(ctx context.Context, organisationID, projectID, kind string) ([]byte, error)
	FinishAlertAttempt(ctx context.Context, d store.AlertDelivery, a store.AlertAttempt) error
}

// Ingress reads what LiveKit reported of an ingress after an error (chstore.Store).
type Ingress interface {
	IngressChanges(ctx context.Context, organisationID, projectID string, ids []string) ([]store.StatusChange, error)
	IngressWebhooks(ctx context.Context, organisationID, projectID string, ids []string, since time.Time) ([]store.WebhookStatus, error)
}

// Mailer sends one plain-text email (mailer.Mailer).
type Mailer interface {
	SendTo(ctx context.Context, to []string, subject, body string) error
}
