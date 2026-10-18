package api

import (
	"fmt"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// #345: a time limit cuts a recording short, it does not lose it; a failure or abort does.
func TestEgressOutcome(t *testing.T) {
	tests := []struct {
		status string
		want   *string
	}{
		{"EGRESS_COMPLETE", ptrTo(outcomeRecorded)},
		{"EGRESS_LIMIT_REACHED", ptrTo(outcomeCutShort)},
		{"EGRESS_FAILED", ptrTo(outcomeLost)},
		{"EGRESS_ABORTED", ptrTo(outcomeLost)},
		{"EGRESS_STARTING", nil},
		{"EGRESS_ACTIVE", nil},
		{"EGRESS_ENDING", nil},
	}
	for _, tt := range tests {
		got := egressOutcome(tt.status)
		if (got == nil) != (tt.want == nil) || (got != nil && *got != *tt.want) {
			t.Errorf("%s reads as %v, want %v", tt.status, deref(got), deref(tt.want))
		}
	}
}

// A status history joins samples and webhooks in LiveKit's time order, the worker's read time breaking
// ties, and keeps the earliest evidence of each change once; a webhook without its own time is placed
// when it was received, and stays null.
func TestHistoriesJoinSamplesAndWebhooks(t *testing.T) {
	at := func(s int) time.Time { return time.Date(2026, 10, 17, 12, 0, s, 0, time.UTC) }
	ptr := func(t time.Time) *time.Time { return &t }
	changes := []store.StatusChange{
		{ID: "EG_1", Status: "EGRESS_ACTIVE", At: at(5), UpdatedAt: ptr(at(3))},
		{ID: "EG_1", Status: "EGRESS_COMPLETE", At: at(20), UpdatedAt: ptr(at(18))},
	}
	hooks := []store.WebhookStatus{
		{ID: "EG_1", Status: "EGRESS_STARTING", At: ptr(at(1)), ReceivedAt: at(2)},
		{ID: "EG_1", Status: "EGRESS_ACTIVE", At: ptr(at(3)), ReceivedAt: at(3)},
		{ID: "EG_1", Status: "EGRESS_ENDING", ReceivedAt: at(15)},
		{ID: "EG_2", Status: "EGRESS_STARTING", At: ptr(at(4)), ReceivedAt: at(4)},
	}
	got := histories(changes, hooks)
	want := "[{12:00:01 EGRESS_STARTING} {12:00:03 EGRESS_ACTIVE} {null EGRESS_ENDING} {12:00:18 EGRESS_COMPLETE}]"
	if s := show(got["EG_1"].history); s != want {
		t.Errorf("EG_1 history = %s, want %s", s, want)
	}
	if got["EG_1"].firstSampled != "EGRESS_ACTIVE" {
		t.Errorf("first sampled = %q, want EGRESS_ACTIVE", got["EG_1"].firstSampled)
	}
	if s := show(got["EG_2"].history); s != "[{12:00:04 EGRESS_STARTING}]" || got["EG_2"].firstSampled != "" {
		t.Errorf("EG_2 history = %s, first sampled %q; want the webhook only", s, got["EG_2"].firstSampled)
	}
}

func show(h []store.StatusAt) string {
	out := "["
	for i, s := range h {
		if i > 0 {
			out += " "
		}
		at := "null"
		if s.At != nil {
			at = s.At.Format("15:04:05")
		}
		out += fmt.Sprintf("{%s %s}", at, s.Status)
	}
	return out + "]"
}

func deref[T any](p *T) any {
	if p == nil {
		return nil
	}
	return *p
}
