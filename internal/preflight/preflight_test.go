package preflight

import (
	"bytes"
	"context"
	"errors"
	"strings"
	"testing"
	"testing/synctest"
	"time"
)

func probe(err error) func(context.Context) error {
	return func(context.Context) error { return err }
}

func TestRunReportsEveryFailureInOrder(t *testing.T) {
	p := Preflight{Timeout: time.Second, Checks: []Check{
		{Name: "postgres", Probe: probe(nil)},
		{Name: "redis", Probe: probe(errors.New("connection refused"))},
		{Name: "clickhouse", Probe: probe(nil)},
		{Name: "s3", Probe: probe(errors.New("bucket recordings does not exist"))},
	}}
	results := p.Run(context.Background())

	var out bytes.Buffer
	ok, err := Report(&out, results)
	if err != nil {
		t.Fatal(err)
	}
	if ok {
		t.Fatal("Report() ok = true with two failures")
	}
	lines := strings.Split(strings.TrimSpace(out.String()), "\n")
	want := []string{"OK   postgres", "FAIL redis", "OK   clickhouse", "FAIL s3"}
	if len(lines) != len(want) {
		t.Fatalf("got %d lines, want %d:\n%s", len(lines), len(want), out.String())
	}
	for i, prefix := range want {
		if !strings.HasPrefix(lines[i], prefix) {
			t.Errorf("line %d = %q, want prefix %q", i, lines[i], prefix)
		}
	}
}

func TestRunBoundsEachCheckByTimeout(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		hang := func(ctx context.Context) error { <-ctx.Done(); return ctx.Err() }
		p := Preflight{Timeout: 3 * time.Second, Checks: []Check{
			{Name: "redis", Probe: hang},
			{Name: "postgres", Probe: probe(nil)},
		}}
		start := time.Now()
		results := p.Run(context.Background())
		if took := time.Since(start); took != 3*time.Second {
			t.Errorf("Run took %s, want exactly the 3s timeout", took)
		}
		if !errors.Is(results[0].Err, context.DeadlineExceeded) {
			t.Errorf("hanging check err = %v, want deadline exceeded", results[0].Err)
		}
		if results[1].Err != nil {
			t.Errorf("healthy check err = %v", results[1].Err)
		}
	})
}
