package pgstore

import (
	"bytes"
	"context"
	"errors"
	"testing"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestResourceEndpointSealed(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	if _, err := s.CreateProject(ctx, project("alpha", "APIalpha")); err != nil {
		t.Fatal(err)
	}
	const raw = "https://scraper:metrics-pass@10.0.0.5:8081/metrics?token=metrics-token"
	e, err := s.AddResourceEndpoint(ctx, "default", "alpha", raw, "worker", "worker-a", "/docker/a")
	if err != nil {
		t.Fatal(err)
	}
	if e.Host != "https://10.0.0.5:8081" || e.CreatedAt.IsZero() || len(e.ID) != len("re_")+12 {
		t.Errorf("AddResourceEndpoint() = %+v", e)
	}

	var host string
	var sealed []byte
	if err := pool.QueryRow(ctx, "SELECT host, url_sealed FROM project_resource_endpoints WHERE id = $1", e.ID).Scan(&host, &sealed); err != nil {
		t.Fatal(err)
	}
	for _, secret := range []string{"metrics-pass", "metrics-token", "scraper", "/metrics"} {
		if bytes.Contains(sealed, []byte(secret)) || bytes.Contains([]byte(host), []byte(secret)) {
			t.Errorf("a stored column contains %q in the clear", secret)
		}
	}

	listed, err := s.ResourceEndpoints(ctx, "default", "alpha")
	if err != nil || len(listed) != 1 || listed[0].ID != e.ID || listed[0].Host != e.Host {
		t.Errorf("ResourceEndpoints() = %+v, %v", listed, err)
	}
	opened, err := s.OpenResourceEndpoints(ctx, "default", "alpha")
	if err != nil || len(opened) != 1 || opened[0].URL != raw {
		t.Errorf("OpenResourceEndpoints() = %+v, %v; want the original URL", opened, err)
	}

	if err := s.RemoveResourceEndpoint(ctx, "other", "alpha", e.ID); !errors.Is(err, store.ErrNotFound) {
		t.Fatal("cross-org removal", err)
	}
	if got, err := s.ResourceEndpoints(ctx, "default", "other"); err != nil || len(got) != 0 {
		t.Fatal("cross-project read", err)
	}
	if _, err := s.AddResourceEndpoint(ctx, "default", "alpha", raw, "worker", "worker-a", "/docker/b"); !errors.Is(err, store.ErrEndpointExists) {
		t.Fatal("ambiguous target mapping", err)
	}
	if _, err := s.AddResourceEndpoint(ctx, "default", "alpha", raw, "worker", "root", "/"); !errors.Is(err, ErrBadResourceTarget) {
		t.Fatal("root cgroup accepted", err)
	}

	// The seal is bound to the row: a URL moved to another endpoint's row does not open.
	if _, err := pool.Exec(ctx, "UPDATE project_resource_endpoints SET id = 're_000000000000' WHERE id = $1", e.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := s.OpenResourceEndpoints(ctx, "default", "alpha"); err == nil {
		t.Error("a sealed URL opened under another endpoint id")
	}
}
