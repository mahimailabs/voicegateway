package ingest

import (
	"context"
	"net/http"
	"strconv"
)

// Quota is wired only on a hosted instance. Authentication runs before it.
type Quota interface {
	Check(context.Context, string) (allowed bool, warningPercent int, err error)
}

func checkQuota(w http.ResponseWriter, r *http.Request, q Quota, org string) bool {
	if q == nil {
		return true
	}
	allowed, warning, err := q.Check(r.Context(), org)
	if err != nil {
		unavailable(w)
		return false
	}
	w.Header().Set("X-VoiceGateway-Usage-Warning", strconv.Itoa(warning))
	if !allowed {
		// 402 is a permanent refusal for this upload, not a busy-loop retry response.
		// It never calls LiveKit or stops the customer's agent.
		http.Error(w, "telemetry allowance unavailable or exhausted; existing data remains readable; contact support for capacity", http.StatusPaymentRequired)
		return false
	}
	return true
}
