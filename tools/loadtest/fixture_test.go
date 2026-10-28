package main

import (
	"strings"
	"testing"
	"time"
)

func TestClockShiftsEveryUnit(t *testing.T) {
	base := time.Unix(1792258913, 0)
	c := clock{base: base, start: time.Unix(1800000000, 0), stretch: 2}
	tests := []struct{ in, want string }{
		{`"createdAt":"1792258913"`, `"createdAt":"1800000000"`},
		{`"joinedAtMs":"1792258914706"`, `"joinedAtMs":"1800000003412"`},
		{`"unixMicro":"1792258913382475"`, `"unixMicro":"1800000000764950"`},
		{`"timeUnixNano":"1792258915000000001"`, `"timeUnixNano":"1800000004000000002"`},
		{`"created_at": 1792258914.324679`, `"created_at": 1800000002.649358`},
		{`"start":"2026-10-17T17:41:54.305Z"`, `"start":"2027-01-15T08:00:02.61Z"`},
		// Not an instant: ids, short numbers and digits inside hex are left alone.
		{`"emptyTimeout":300,"trace_id":"ab1792258913cd"`, `"emptyTimeout":300,"trace_id":"ab1792258913cd"`},
	}
	for _, tt := range tests {
		if got := c.shift(tt.in); got != tt.want {
			t.Errorf("shift(%s) = %s, want %s", tt.in, got, tt.want)
		}
	}
}

func TestPlansShareNoIDs(t *testing.T) {
	f, err := loadFixture("../..")
	if err != nil {
		t.Fatal(err)
	}
	start := time.Now()
	ids := map[string]int{}
	for n := range 2 {
		steps, err := f.plan("Test", n, start, 1, true)
		if err != nil {
			t.Fatal(err)
		}
		// 10 webhooks, the session report, the SDK batch and 2 metric exports, plus span batches.
		kinds := map[kind]int{}
		for i, s := range steps {
			kinds[s.kind]++
			if i > 0 && s.at.Before(steps[i-1].at) {
				t.Fatalf("call %d: step %d sent before the one ahead of it", n, i)
			}
			if s.at.Before(start) {
				t.Fatalf("call %d: %s sent before the call starts", n, s.kind)
			}
		}
		if kinds[kindWebhook] != len(f.webhooks) || kinds[kindReport] != 1 || kinds[kindSDK] != 1 || kinds[kindMetrics] != len(metricFixtures) {
			t.Fatalf("call %d sends %v", n, kinds)
		}
		for _, s := range steps {
			if s.kind != kindWebhook {
				continue
			}
			for _, id := range []string{s.webhook.GetId(), s.webhook.GetRoom().GetSid(), s.webhook.GetParticipant().GetSid()} {
				if id == "" {
					continue
				}
				if !strings.Contains(id, "_Test") {
					t.Errorf("id %s kept the captured value", id)
				}
				if other, ok := ids[id]; ok && other != n {
					t.Errorf("calls %d and %d share id %s", other, n, id)
				}
				ids[id] = n
			}
		}
	}
}
