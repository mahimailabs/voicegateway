package bucket

import (
	"context"
	"log/slog"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Links forgets the links to recordings stored before a cutoff.
type Links interface {
	ForgetRecordings(ctx context.Context, cutoff time.Time) (int64, error)
}

// Retention deletes recordings older than store.RecordingKeep: their links first, so the API never offers one
// whose audio is gone, then their objects, by day.
type Retention struct {
	Bucket *Bucket
	Links  Links
	Log    *slog.Logger
}

// Run deletes what expired by now. Safe to run on every worker at once.
func (r *Retention) Run(ctx context.Context, now time.Time) error {
	cutoff := now.Add(-store.RecordingKeep)
	links, err := r.Links.ForgetRecordings(ctx, cutoff)
	if err != nil {
		return err
	}
	objects, err := r.Bucket.Expire(ctx, cutoff)
	if links > 0 || objects > 0 {
		r.Log.Info("recordings expired", "links", links, "objects", objects)
	}
	return err
}
