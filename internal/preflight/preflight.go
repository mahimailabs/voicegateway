package preflight

import (
	"context"
	"fmt"
	"io"
	"sync"
	"time"
)

// Check is one dependency: a name for the report and a probe that returns nil when it answers.
type Check struct {
	Name   string
	Target string // shown in the report; never carries a secret
	Probe  func(ctx context.Context) error
}

// Result is the outcome of one check.
type Result struct {
	Name   string
	Target string
	Took   time.Duration
	Err    error
}

// Preflight runs checks concurrently, each bounded by Timeout.
type Preflight struct {
	Checks  []Check
	Timeout time.Duration
}

// Run runs every check and returns the results in the order the checks were given.
func (p Preflight) Run(ctx context.Context) []Result {
	results := make([]Result, len(p.Checks))
	var wg sync.WaitGroup
	for i, c := range p.Checks {
		wg.Go(func() {
			cctx, cancel := context.WithTimeout(ctx, p.Timeout)
			defer cancel()
			start := time.Now()
			err := c.Probe(cctx)
			if err == nil && cctx.Err() != nil {
				err = cctx.Err()
			}
			results[i] = Result{Name: c.Name, Target: c.Target, Took: time.Since(start), Err: err}
		})
	}
	wg.Wait()
	return results
}

// Report writes one line per result and returns whether every check passed.
func Report(w io.Writer, results []Result) (bool, error) {
	ok := true
	for _, r := range results {
		var err error
		if r.Err != nil {
			ok = false
			_, err = fmt.Fprintf(w, "FAIL %-10s %s: %v\n", r.Name, r.Target, r.Err)
		} else {
			_, err = fmt.Fprintf(w, "OK   %-10s %s (%s)\n", r.Name, r.Target, r.Took.Round(time.Millisecond))
		}
		if err != nil {
			return false, fmt.Errorf("write preflight report: %w", err)
		}
	}
	return ok, nil
}
