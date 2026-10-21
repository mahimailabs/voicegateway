package ingest

import (
	"context"
	"net/http"
	"strconv"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// Quota is wired only on a hosted instance. Authentication runs before it.
type Quota interface {
	Check(context.Context, string) (allowed bool, warningPercent int, err error)
}

type recordingQuota interface {
	Remember(context.Context, string, string, event.Kind, []byte) error
}

// refusingQuota names why an organisation is refused: store.ReasonPlanInactive or
// store.ReasonPlanCap. A quota without it is reported as at its cap.
type refusingQuota interface {
	Refusal(context.Context, string) string
}

type continuingQuota interface {
	Continue(context.Context, string, string, event.Kind, []byte) (bool, error)
}

func checkQuota(w http.ResponseWriter, r *http.Request, q Quota, org string) bool {
	ok, _ := checkEnvelopeQuota(w, r, q, org, "", "", nil)
	return ok
}

// checkEnvelopeQuota answers the request itself and returns false when it may not proceed, with
// the refusal's reason code when the quota refused it, or "" when the quota could not answer.
func checkEnvelopeQuota(w http.ResponseWriter, r *http.Request, q Quota, org, project string, kind event.Kind, payload []byte) (bool, string) {
	if q == nil {
		return true, ""
	}
	allowed, warning, err := q.Check(r.Context(), org)
	if err != nil {
		unavailable(w)
		return false, ""
	}
	if allowed && project != "" {
		if recorder, ok := q.(recordingQuota); ok {
			if err := recorder.Remember(r.Context(), org, project, kind, payload); err != nil {
				unavailable(w)
				return false, ""
			}
		}
	}
	if !allowed && project != "" {
		if continuation, ok := q.(continuingQuota); ok {
			allowed, err = continuation.Continue(r.Context(), org, project, kind, payload)
			if err != nil {
				unavailable(w)
				return false, ""
			}
		}
	}
	w.Header().Set("X-VoiceGateway-Usage-Warning", strconv.Itoa(warning))
	if !allowed {
		// 402 is a permanent refusal for this upload, not a busy-loop retry response.
		// It never calls LiveKit or stops the customer's agent.
		http.Error(w, "telemetry allowance unavailable or exhausted; existing data remains readable; contact support for capacity", http.StatusPaymentRequired)
		reason := store.ReasonPlanCap
		if refusing, ok := q.(refusingQuota); ok {
			reason = refusing.Refusal(r.Context(), org)
		}
		return false, reason
	}
	return true, ""
}
