package scrape

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

type JobFindingStore interface {
	PendingJobFindings(context.Context, int) ([]store.PendingJobFinding, error)
	ProjectSettings(context.Context, string, string) (store.ProjectSettings, error)
	PutJobFindings(context.Context, store.PendingJobFinding, []store.JobFinding, []string, int64) error
}

type JobEvaluator struct {
	Store   JobFindingStore
	Changed func(context.Context, string, string)
}

// Tick evaluates a bounded batch. A later native participant webhook requeues an observation
// through its exact room SID and participant identity, without depending on the room staying open.
func (e *JobEvaluator) Tick(ctx context.Context, _ time.Time) error {
	jobs, err := e.Store.PendingJobFindings(ctx, 100)
	if err != nil {
		return err
	}
	var failures []error
	for _, job := range jobs {
		if err := ctx.Err(); err != nil {
			return err
		}
		settings, err := e.Store.ProjectSettings(ctx, job.OrganisationID, job.ProjectID)
		if err != nil {
			failures = append(failures, err)
			continue
		}
		findings, covered := jobFindings(job, settings.Thresholds)
		if err = e.Store.PutJobFindings(ctx, job, findings, covered, settings.Revision); err != nil {
			failures = append(failures, err)
			continue
		}
		if e.Changed != nil {
			e.Changed(ctx, job.OrganisationID, job.ProjectID)
		}
	}
	return errors.Join(failures...)
}

func jobFindings(job store.PendingJobFinding, thresholds store.Thresholds) ([]store.JobFinding, []string) {
	findings := []store.JobFinding{}
	covered := []string{}
	if job.FailureObservedAt != nil {
		at := job.ReportedFailedAt
		detail := "LiveKit reported JS_FAILED for this job."
		if at == nil {
			at = job.FailureObservedAt
			detail += " Timestamp is first observation; failure time was not reported."
		}
		findings = append(findings, store.JobFinding{Finding: store.Finding{ID: "job-failed", Severity: "high", Title: "Agent job failed", Detail: detail}, At: at})
		covered = append(covered, "job-failed")
	}
	if job.DispatchedAt != nil && job.JoinedAt != nil && !job.JoinedAt.Before(*job.DispatchedAt) {
		covered = append(covered, "slow-join")
		ms := float64(job.JoinedAt.Sub(*job.DispatchedAt)) / float64(time.Millisecond)
		if ms > thresholds.JoinMs {
			findings = append(findings, store.JobFinding{Finding: store.Finding{ID: "slow-join", Severity: "medium", Title: "Agent dispatch to join exceeded threshold", Detail: fmt.Sprintf("Native dispatch to participant join: %.0f ms (threshold %.0f ms). Correlated by reported participant identity within this call.", ms, thresholds.JoinMs)}, At: job.JoinedAt})
		}
	}
	return findings, covered
}
