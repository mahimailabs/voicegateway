package alert

import (
	"net/http"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// A 429 or 5xx with Retry-After waits at least as long as the server asks, within the 24-hour
// window; without it the schedule is 30 s, 1 min, 2 min, doubling to 30 min.
func TestRetrySchedule(t *testing.T) {
	now := time.Date(2026, 10, 28, 12, 0, 0, 0, time.UTC)
	d := &Deliverer{}
	del := store.AlertDelivery{CreatedAt: now}
	tests := []struct {
		name     string
		attempts int
		o        outcome
		want     time.Duration
		state    string
	}{
		{"first retry", 0, outcome{retry: true}, 30 * time.Second, "retrying"},
		{"third retry", 2, outcome{retry: true}, 2 * time.Minute, "retrying"},
		{"capped", 12, outcome{retry: true}, 30 * time.Minute, "retrying"},
		{"retry-after longer", 0, outcome{retry: true, retryAfter: 5 * time.Minute}, 5 * time.Minute, "retrying"},
		{"retry-after shorter", 3, outcome{retry: true, retryAfter: time.Second}, 4 * time.Minute, "retrying"},
		{"refused", 0, outcome{status: "HTTP 400"}, 0, "failed"},
	}
	for _, tt := range tests {
		del.Attempts = tt.attempts
		a := d.schedule(del, tt.o, now)
		if a.State != tt.state || a.NextAttemptAt.Sub(now) != tt.want {
			t.Errorf("%s: %s after %s, want %s after %s", tt.name, a.State, a.NextAttemptAt.Sub(now), tt.state, tt.want)
		}
	}
	if a := d.schedule(del, outcome{retry: true}, now.Add(25*time.Hour)); a.State != "failed" || a.Broken || a.Issue == "" {
		t.Errorf("after 24 h: %+v, want failed, not broken, listed on Issues", a)
	}
	for v, want := range map[string]time.Duration{"120": 2 * time.Minute, "": 0, "soon": 0, now.Add(time.Hour).Format(http.TimeFormat): time.Hour} {
		if got := retryAfter(v, now); got != want {
			t.Errorf("Retry-After %q = %s, want %s", v, got, want)
		}
	}
}
