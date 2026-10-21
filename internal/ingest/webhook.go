package ingest

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"time"

	"github.com/livekit/protocol/livekit"
	"github.com/livekit/protocol/utils/protojson"
	"github.com/livekit/protocol/webhook"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// maxWebhookBody bounds a webhook body. LiveKit's largest, a room or egress event, is a few KiB.
const maxWebhookBody = 1 << 20

// Projects finds the project that signed a request and opens its LiveKit secret.
type Projects interface {
	ProjectByLiveKitKey(ctx context.Context, key string) (store.Project, error)
	LiveKitSecret(ctx context.Context, organisationID, projectID string) (string, error)
}

// Appender puts a verified envelope on the stream.
type Appender interface {
	Append(ctx context.Context, e event.Envelope) (string, error)
}

// Webhooks receives LiveKit webhooks. The JWT in Authorization must be signed by a project's LiveKit
// key and carry the body's SHA-256; livekit/protocol's webhook.Receive checks both.
type Webhooks struct {
	Quota    Quota
	Projects Projects
	Stream   Appender
	// Outcome records whether the webhook was accepted or why it was refused; the zero value
	// records nothing.
	Outcome Outcome
	Log     *slog.Logger
	Now     func() time.Time
}

func (h *Webhooks) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, maxWebhookBody)
	keys := &projectKeys{ctx: r.Context(), projects: h.Projects}
	body, err := webhook.Receive(r, keys)
	if keys.err != nil {
		h.Log.Error("webhook: project lookup failed", "err", keys.err)
		http.Error(w, "project lookup failed", http.StatusServiceUnavailable)
		return
	}
	if mbe := (*http.MaxBytesError)(nil); errors.As(err, &mbe) {
		h.Log.Warn("webhook: body too large", "limit", mbe.Limit)
		h.Outcome.Rejected(r.Context(), store.ReasonTooLarge, "")
		http.Error(w, "body too large", http.StatusRequestEntityTooLarge)
		return
	}
	if err != nil {
		// The reason is safe to log: it names the check that failed, never the body or a secret.
		h.Log.Warn("webhook: not verified", "reason", err.Error())
		reason, usedKey := webhookRefusal(err, keys)
		h.Outcome.Rejected(r.Context(), reason, usedKey)
		http.Error(w, "webhook not verified", http.StatusUnauthorized)
		return
	}

	if ok, reason := checkEnvelopeQuota(w, r, h.Quota, keys.project.OrganisationID, keys.project.ID, event.KindLiveKitWebhook, body); !ok {
		if reason != "" {
			h.Outcome.Rejected(r.Context(), reason, "")
		}
		return
	}

	var ev livekit.WebhookEvent
	if err := (protojson.UnmarshalOptions{DiscardUnknown: true, AllowPartial: true}).Unmarshal(body, &ev); err != nil {
		h.Log.Warn("webhook: verified body is not a webhook event", "project", keys.project.ID)
		http.Error(w, "not a webhook event", http.StatusBadRequest)
		return
	}
	id, err := h.Stream.Append(r.Context(), event.Envelope{
		ID:             ev.GetId(),
		Kind:           event.KindLiveKitWebhook,
		OrganisationID: keys.project.OrganisationID,
		ProjectID:      keys.project.ID,
		ReceivedAt:     h.Now(),
		Payload:        body,
	})
	if err != nil {
		// LiveKit retries a failed delivery, so a 503 loses nothing.
		h.Log.Error("webhook: append failed", "project", keys.project.ID, "event", ev.GetEvent(), "err", err)
		http.Error(w, "stream unavailable", http.StatusServiceUnavailable)
		return
	}
	h.Outcome.Accepted(r.Context())
	h.Log.Debug("webhook appended", "project", keys.project.ID, "event", ev.GetEvent(), "event_id", ev.GetId(), "entry", id)
	w.WriteHeader(http.StatusOK)
}

// webhookRefusal names the reason code for an error from webhook.Receive, by livekit/protocol's own
// error values. Any other error is from parsing or verifying the token.
func webhookRefusal(err error, keys *projectKeys) (reason, usedKey string) {
	switch {
	case errors.Is(err, webhook.ErrNoAuthHeader):
		return store.ReasonTokenMissing, ""
	case errors.Is(err, webhook.ErrSecretNotFound):
		return store.ReasonKeyMismatch, keys.usedKey
	case errors.Is(err, webhook.ErrInvalidChecksum):
		return store.ReasonBodyChanged, ""
	}
	return store.ReasonSignatureInvalid, ""
}

// projectKeys is the auth.KeyProvider for one request. It remembers which project owns the key, so
// the envelope is attributed to the project that signed it, and a storage failure, so that is
// answered as unavailable rather than as a bad signature.
type projectKeys struct {
	ctx      context.Context
	projects Projects
	project  store.Project
	err      error
	// usedKey is the key the token named, kept for a key mismatch.
	usedKey string
}

func (k *projectKeys) GetSecret(key string) string {
	k.usedKey = key
	p, err := k.projects.ProjectByLiveKitKey(k.ctx, key)
	if errors.Is(err, store.ErrNotFound) {
		return ""
	}
	if err != nil {
		k.err = err
		return ""
	}
	secret, err := k.projects.LiveKitSecret(k.ctx, p.OrganisationID, p.ID)
	if err != nil {
		k.err = err
		return ""
	}
	k.project = p
	return secret
}

// NumKeys is part of auth.KeyProvider; webhook.Receive does not use it.
func (k *projectKeys) NumKeys() int { return 1 }
