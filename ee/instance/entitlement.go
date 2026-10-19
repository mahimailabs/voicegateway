package instance

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"time"

	"github.com/mahimailabs/voicegateway/internal/api"
	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// Entitlements is the hosted allowance store.
type Entitlements interface {
	PutEntitlement(context.Context, string, store.Entitlement) error
	EntitlementUsage(context.Context, string) (store.EntitlementUsage, error)
}

// Quota gates ingestion using persisted, closed-call usage. Open calls are unmeasured;
// already accepted concurrent work may close after the cap, without an overage charge.
type Quota struct {
	Store Entitlements
	Now   func() time.Time
}

// Check fails closed for missing, inactive or expired entitlement. A storage failure is
// distinct from exhaustion so the receiver can answer a retryable 503.
func (q *Quota) Check(ctx context.Context, org string) (bool, int, error) {
	u, err := q.Store.EntitlementUsage(ctx, org)
	if errors.Is(err, store.ErrNotFound) {
		return false, 0, nil
	}
	if err != nil {
		return false, 0, err
	}
	return allowed(u, q.Now()), warning(u), nil
}

func allowed(u store.EntitlementUsage, now time.Time) bool {
	return u.Active && !now.Before(u.From) && now.Before(u.To) && u.CallSeconds < u.LimitMinutes*60
}

func warning(u store.EntitlementUsage) int {
	if u.CallSeconds >= u.LimitMinutes*60 {
		return 100
	}
	if u.CallSeconds >= u.LimitMinutes*57 {
		return 95
	}
	if u.CallSeconds >= u.LimitMinutes*48 {
		return 80
	}
	return 0
}

func (h *Handlers) putEntitlement(w http.ResponseWriter, r *http.Request) {
	id, ok := orgID(w, r)
	if !ok {
		return
	}
	var body struct {
		From         *time.Time `json:"from"`
		To           *time.Time `json:"to"`
		LimitMinutes *int64     `json:"limitMinutes"`
		Active       *bool      `json:"active"`
		Revision     *int64     `json:"revision"`
	}
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxBody))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&body); err != nil || body.From == nil || body.To == nil || body.LimitMinutes == nil || body.Active == nil || body.Revision == nil {
		api.WriteError(w, r, 400, api.CodeInvalidRequest, "from, to, limitMinutes, active and revision are required")
		return
	}
	if err := dec.Decode(new(any)); err != io.EOF {
		api.WriteError(w, r, 400, api.CodeInvalidRequest, "one JSON object is required")
		return
	}
	e := store.Entitlement{From: body.From.UTC().Truncate(time.Microsecond), To: body.To.UTC().Truncate(time.Microsecond), LimitMinutes: *body.LimitMinutes, Active: *body.Active, Revision: *body.Revision}
	if e.From.IsZero() || !e.To.After(e.From) || e.To.Sub(e.From) > maxUsageSpan || e.LimitMinutes < 0 || e.LimitMinutes > 1000000000 || e.Revision < 1 {
		api.WriteError(w, r, 400, api.CodeInvalidRequest, "an explicit period of at most 62 days, limitMinutes 0..1000000000 and positive revision are required")
		return
	}
	err := h.Entitlements.PutEntitlement(r.Context(), id, e)
	switch {
	case errors.Is(err, store.ErrNotFound):
		api.WriteError(w, r, 404, api.CodeNotFound, "no such organisation")
	case errors.Is(err, store.ErrEntitlementRevision):
		api.WriteError(w, r, 409, api.CodeRevisionConflict, "stale or conflicting entitlement revision")
	case err != nil:
		h.fail(w, r, "putEntitlement", id, err)
	default:
		h.getEntitlement(w, r)
	}
}

func (h *Handlers) getEntitlement(w http.ResponseWriter, r *http.Request) {
	id, ok := orgID(w, r)
	if !ok {
		return
	}
	u, err := h.Entitlements.EntitlementUsage(r.Context(), id)
	if errors.Is(err, store.ErrNotFound) {
		api.WriteError(w, r, 404, api.CodeNotFound, "no entitlement")
		return
	}
	if err != nil {
		h.fail(w, r, "getEntitlement", id, err)
		return
	}
	api.WriteJSON(w, 200, struct {
		store.EntitlementUsage
		WarningPercent int  `json:"warningPercent"`
		IngestAllowed  bool `json:"ingestAllowed"`
	}{u, warning(u), allowed(u, time.Now())})
}

// Continue allows a known call to finish while an active current allowance is exhausted.
// Revocation and expiry never inherit this exception. Mixed exports must name only known calls.
func (q *Quota) Continue(ctx context.Context, org, project string, kind event.Kind, payload []byte) (bool, error) {
	u, err := q.Store.EntitlementUsage(ctx, org)
	if errors.Is(err, store.ErrNotFound) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	now := q.Now()
	if !u.Active || now.Before(u.From) || !now.Before(u.To) {
		return false, nil
	}
	refs, err := event.CallIdentities(kind, payload)
	if err != nil {
		return false, nil
	}
	known, ok := q.Store.(interface {
		KnownQuotaCalls(context.Context, string, string, []event.CallIdentity, time.Time, time.Time) (bool, error)
	})
	if !ok {
		return false, nil
	}
	return known.KnownQuotaCalls(ctx, org, project, refs, u.From, u.To)
}

// Remember persists identified calls before the stream append. A retried append therefore
// keeps its admission if the worker is behind when another call exhausts the allowance.
func (q *Quota) Remember(ctx context.Context, org, project string, kind event.Kind, payload []byte) error {
	refs, err := event.CallIdentities(kind, payload)
	if err != nil {
		return nil
	} // Unattributed data is accepted below the cap, but cannot earn an exception.
	recorder, ok := q.Store.(interface {
		RememberQuotaCalls(context.Context, string, string, []event.CallIdentity, time.Time) error
	})
	if !ok {
		return errors.New("quota admission store unavailable")
	}
	return recorder.RememberQuotaCalls(ctx, org, project, refs, q.Now())
}
