package sink

import (
	"context"
	"testing"
	"testing/synctest"
	"time"
)

// TestSleepJitters checks that a backoff wait lands between half the step and the whole step, so
// workers that failed together spread out instead of retrying together.
func TestSleepJitters(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		s := &Sink{}
		const step = time.Second
		seen := map[time.Duration]bool{}
		for range 50 {
			start := time.Now()
			s.sleep(context.Background(), step)
			got := time.Since(start)
			if got < step/2 || got > step {
				t.Fatalf("slept %v, want between %v and %v", got, step/2, step)
			}
			seen[got] = true
		}
		if len(seen) < 2 {
			t.Fatal("all 50 waits were the same length; want them to vary")
		}
	})
}
