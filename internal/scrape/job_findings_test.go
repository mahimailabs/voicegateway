package scrape

import (
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestJobFindingsUnknownIsNotRecovery(t *testing.T) {
	at := time.Now()
	earlier := at.Add(-time.Second)
	for _, job := range []store.PendingJobFinding{{}, {DispatchedAt: &at}, {JoinedAt: &at}, {DispatchedAt: &at, JoinedAt: &earlier}} {
		findings, covered := jobFindings(job, store.Thresholds{JoinMs: 1000})
		if len(findings) != 0 || len(covered) != 0 {
			t.Fatal("unknown or negative join claimed coverage")
		}
	}
	findings, _ := jobFindings(store.PendingJobFinding{FailureObservedAt: &at}, store.Thresholds{JoinMs: 1000})
	if len(findings) != 1 || findings[0].At != &at || findings[0].Detail == "LiveKit reported JS_FAILED for this job." {
		t.Fatal("observation timestamp not distinguished from native failure time")
	}
}
