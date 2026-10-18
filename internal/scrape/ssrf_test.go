package scrape

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"syscall"
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

// The dial decision per resolved address, in both modes, asked of the Control function itself:
// no socket is opened, so it holds on a host without IPv6.
func TestWorkerDialGuardDecisions(t *testing.T) {
	selfHosted, publicOnly := dialGuard(false), dialGuard(true)
	for _, tt := range []struct {
		address    string
		selfHosted error // nil means allowed
		publicOnly error
	}{
		{"127.0.0.1:9100", nil, ssrf.ErrProhibitedIP},
		{"10.0.0.7:9100", nil, ssrf.ErrProhibitedIP},
		{"172.18.0.4:9100", nil, ssrf.ErrProhibitedIP},
		{"192.168.1.20:9100", nil, ssrf.ErrProhibitedIP},
		{"[fd12:3456::7]:9100", nil, ssrf.ErrProhibitedIP},
		{"[::1]:9100", nil, ssrf.ErrProhibitedIP},
		{"8.8.8.8:443", nil, nil},
		// Metadata services, refused in both modes.
		{"169.254.169.254:80", ssrf.ErrProhibitedIP, ssrf.ErrProhibitedIP}, // AWS, GCP, Azure, OCI
		{"169.254.170.2:80", ssrf.ErrProhibitedIP, ssrf.ErrProhibitedIP},   // ECS task metadata
		{"100.100.100.200:80", errMetadataAddress, errMetadataAddress},     // Alibaba Cloud, in 100.64.0.0/10
		{"[fd00:ec2::254]:80", errMetadataAddress, errMetadataAddress},     // AWS IPv6, in fc00::/7
		{"[fd20:ce::254]:80", errMetadataAddress, errMetadataAddress},      // GCP IPv6, in fc00::/7
		{"[fe80::1]:9100", ssrf.ErrProhibitedIP, ssrf.ErrProhibitedIP},     // IPv6 link-local
		{"[::ffff:169.254.169.254]:80", ssrf.ErrProhibitedIP, ssrf.ErrProhibitedIP},
	} {
		network := "tcp4"
		if strings.HasPrefix(tt.address, "[") {
			network = "tcp6"
		}
		for name, c := range map[string]struct {
			guard func(string, string, syscall.RawConn) error
			want  error
		}{"self-hosted": {selfHosted, tt.selfHosted}, "public-only": {publicOnly, tt.publicOnly}} {
			got := c.guard(network, tt.address, nil)
			if (c.want == nil) != (got == nil) || (c.want != nil && !errors.Is(got, c.want)) {
				t.Errorf("%s, %s: %v, want %v", name, tt.address, got, c.want)
			}
		}
	}
}

// End to end over a real loopback dial: read by default, refused in public-only mode.
func TestWorkerClientDialGuard(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) }))
	defer srv.Close()
	if err := get(t, NewWorkerClient(false), srv.URL); err != nil {
		t.Errorf("self-hosted, loopback worker: %v, want it read", err)
	}
	if err := get(t, NewWorkerClient(true), srv.URL); !errors.Is(err, ssrf.ErrProhibitedIP) {
		t.Errorf("public-only, loopback worker: %v, want refused by the guard", err)
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
