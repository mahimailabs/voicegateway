package pgstore

import (
	"bytes"
	"context"
	"errors"
	"testing"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// A worker metrics URL is sealed at rest, bound to its row; only its scheme and host are stored in
// the clear, and only the worker's read opens it.
func TestWorkerEndpointSealed(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	if _, err := s.CreateProject(ctx, project("alpha", "APIalpha")); err != nil {
		t.Fatal(err)
	}
	const raw = "https://scraper:metrics-pass@10.0.0.5:8081/metrics?token=metrics-token"
	e, err := s.AddWorkerEndpoint(ctx, "default", "alpha", raw)
	if err != nil {
		t.Fatal(err)
	}
	if e.Host != "https://10.0.0.5:8081" || e.CreatedAt.IsZero() || len(e.ID) != len("we_")+12 {
		t.Errorf("AddWorkerEndpoint() = %+v", e)
	}

	var host string
	var sealed []byte
	if err := pool.QueryRow(ctx, "SELECT host, url_sealed FROM project_worker_endpoints WHERE id = $1", e.ID).Scan(&host, &sealed); err != nil {
		t.Fatal(err)
	}
	for _, secret := range []string{"metrics-pass", "metrics-token", "scraper", "/metrics"} {
		if bytes.Contains(sealed, []byte(secret)) || bytes.Contains([]byte(host), []byte(secret)) {
			t.Errorf("a stored column contains %q in the clear", secret)
		}
	}

	listed, err := s.WorkerEndpoints(ctx, "default", "alpha")
	if err != nil || len(listed) != 1 || listed[0].ID != e.ID || listed[0].Host != e.Host {
		t.Errorf("WorkerEndpoints() = %+v, %v", listed, err)
	}
	opened, err := s.OpenWorkerEndpoints(ctx, "default", "alpha")
	if err != nil || len(opened) != 1 || opened[0].URL != raw {
		t.Errorf("OpenWorkerEndpoints() = %+v, %v; want the original URL", opened, err)
	}

	// The seal is bound to the row: a URL moved to another endpoint's row does not open.
	if _, err := pool.Exec(ctx, "UPDATE project_worker_endpoints SET id = 'we_000000000000' WHERE id = $1", e.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := s.OpenWorkerEndpoints(ctx, "default", "alpha"); err == nil {
		t.Error("a sealed URL opened under another endpoint id")
	}
}

func TestWorkerEndpointRules(t *testing.T) {
	s, _ := migrated(t)
	ctx := context.Background()
	if _, err := s.CreateProject(ctx, project("alpha", "APIalpha")); err != nil {
		t.Fatal(err)
	}
	first, err := s.AddWorkerEndpoint(ctx, "default", "alpha", "http://worker-a:8081/metrics")
	if err != nil {
		t.Fatal(err)
	}
	for _, tt := range []struct {
		name, project, url string
		want               error
	}{
		{"same URL twice", "alpha", "http://worker-a:8081/metrics", store.ErrEndpointExists},
		{"not http", "alpha", "ftp://worker-a/metrics", ErrBadEndpointURL},
		{"no host", "alpha", "http:///metrics", ErrBadEndpointURL},
		{"relative", "alpha", "worker-a:8081/metrics", ErrBadEndpointURL},
		{"unknown project", "beta", "http://worker-b:8081/metrics", store.ErrNotFound},
	} {
		t.Run(tt.name, func(t *testing.T) {
			_, err := s.AddWorkerEndpoint(ctx, "default", tt.project, tt.url)
			if !errors.Is(err, tt.want) {
				t.Errorf("AddWorkerEndpoint(%q) err = %v, want %v", tt.url, err, tt.want)
			}
		})
	}
	if _, err := s.AddWorkerEndpoint(ctx, "default", "alpha", "http://worker-b:8081/metrics"); err != nil {
		t.Errorf("a second URL: %v", err)
	}

	if err := s.RemoveWorkerEndpoint(ctx, "default", "alpha", first.ID); err != nil {
		t.Fatal(err)
	}
	if err := s.RemoveWorkerEndpoint(ctx, "default", "alpha", first.ID); !errors.Is(err, store.ErrNotFound) {
		t.Errorf("removing twice err = %v, want ErrNotFound", err)
	}
	listed, err := s.WorkerEndpoints(ctx, "default", "alpha")
	if err != nil || len(listed) != 1 || listed[0].Host != "http://worker-b:8081" {
		t.Errorf("after remove: %+v, %v", listed, err)
	}
}
