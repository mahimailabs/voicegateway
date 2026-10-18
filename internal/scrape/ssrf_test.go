package scrape

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"code.dny.dev/ssrf"
)

// get asks client for url and returns the dial error, if any, without following anything.
func get(t *testing.T, client *http.Client, url string) error {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		t.Fatal(err)
	}
	res, err := client.Do(req)
	if err == nil {
		_ = res.Body.Close()
	}
	return err
}

// The worker endpoint client refuses link-local and metadata addresses always, and private and
// loopback ones only in public-only mode; it is checked at dial time, after DNS.
func TestWorkerClientDialGuard(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) }))
	defer srv.Close()

	selfHosted, publicOnly := NewWorkerClient(false), NewWorkerClient(true)
	if err := get(t, selfHosted, srv.URL); err != nil {
		t.Errorf("self-hosted, loopback worker: %v, want it read", err)
	}
	if err := get(t, publicOnly, srv.URL); !errors.Is(err, ssrf.ErrProhibitedIP) {
		t.Errorf("public-only, loopback worker: %v, want refused by the guard", err)
	}
	for _, url := range []string{
		"http://169.254.169.254/latest/meta-data/", // AWS, GCP and Azure IPv4 metadata
		"http://169.254.170.2/v2/credentials",      // ECS task metadata
		"http://[fe80::1]:9100/metrics",            // IPv6 link-local
	} {
		for name, client := range map[string]*http.Client{"self-hosted": selfHosted, "public-only": publicOnly} {
			if err := get(t, client, url); !errors.Is(err, ssrf.ErrProhibitedIP) {
				t.Errorf("%s, %s: %v, want refused by the guard", name, url, err)
			}
		}
	}
	// AWS's IPv6 metadata address sits inside the private range a self-hosted install allows.
	if err := get(t, selfHosted, "http://[fd00:ec2::254]/latest/meta-data/"); !errors.Is(err, errMetadataAddress) {
		t.Errorf("self-hosted, AWS IPv6 metadata: %v, want refused as metadata", err)
	}
}

// The guard is not bypassed by a proxy from the environment.
func TestWorkerClientIgnoresProxyEnvironment(t *testing.T) {
	t.Setenv("HTTP_PROXY", "http://127.0.0.1:9")
	t.Setenv("http_proxy", "http://127.0.0.1:9")
	if err := get(t, NewWorkerClient(false), "http://169.254.169.254/"); !errors.Is(err, ssrf.ErrProhibitedIP) {
		t.Errorf("with a proxy set, metadata: %v, want refused by the guard, not sent to the proxy", err)
	}
}
