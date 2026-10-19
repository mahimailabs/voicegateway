package auth_test

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"

	"github.com/mahimailabs/voicegateway/internal/scrape"
)

// The LiveKit server the api package's tests run, by tag and digest; --dev gives it devkey and
// secret.
const (
	livekitImage = "livekit/livekit-server:v1.13.3@sha256:483b8b7b5b0654f91f1e8bdc7b46fcd37fd9911612ecf627f97e3185a89825bd"
	devKey       = "devkey"
	devSecret    = "secret"
)

// acceptLiveKit verifies every secret, for tests not about the LiveKit read; the read itself is
// tested against livekit-server --dev below.
type acceptLiveKit struct{}

func (acceptLiveKit) VerifyLiveKit(context.Context, string, string, string) error { return nil }

// liveKitServer starts livekit-server --dev and returns its HTTP URL. Call it after newFixture,
// which skips without Docker outside CI.
func liveKitServer(t *testing.T) string {
	t.Helper()
	ctx := context.Background()
	lk, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: testcontainers.ContainerRequest{
			Image:        livekitImage,
			Cmd:          []string{"--dev", "--bind", "0.0.0.0"},
			ExposedPorts: []string{"7880/tcp"},
			WaitingFor:   wait.ForHTTP("/").WithPort("7880/tcp"),
		},
		Started: true,
	})
	testcontainers.CleanupContainer(t, lk)
	if err != nil {
		t.Fatalf("start livekit-server: %v", err)
	}
	host, err := lk.Host(ctx)
	if err != nil {
		t.Fatal(err)
	}
	port, err := lk.MappedPort(ctx, "7880/tcp")
	if err != nil {
		t.Fatal(err)
	}
	return fmt.Sprintf("http://%s:%s", host, port.Port())
}

// closedURL is an http URL on a loopback port nothing listens on.
func closedURL(t *testing.T) string {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := l.Addr().String()
	_ = l.Close()
	return "http://" + addr
}

func errorCode(b []byte) string {
	var e struct{ Code string }
	_ = json.Unmarshal(b, &e)
	return e.Code
}

func TestFakeLiveKitEndpointCannotReserveAnotherProjectsKey(t *testing.T) {
	f := newFixture(t)
	lk := liveKitServer(t)
	f.addUser("owner@example.com", "org-a", "admin")
	f.addUser("other@example.com", "org-b", "admin")
	s := f.service()
	web := f.membersAPIWith(s, scrape.NewVerifier(false))
	// This endpoint accepts every credential. Private endpoints are allowed here
	// to use a local test server; a public attacker endpoint behaves identically.
	fake := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/protobuf")
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(fake.Close)
	for _, tc := range []struct{ org, email, url, secret string }{
		{"org-b", "other@example.com", fake.URL, "made-up-secret"},
		{"org-a", "owner@example.com", lk, devSecret},
	} {
		status, body := f.send(web, call{
			method: http.MethodPost, path: "/api/v1/org/" + tc.org + "/projects",
			body: projectBody("Production", tc.url, devKey, tc.secret), cookie: f.signedIn(s, tc.email),
		})
		if status != http.StatusCreated {
			t.Fatalf("%s: %d %s", tc.org, status, body)
		}
	}
	if n := f.count(`SELECT count(*) FROM projects`); n != 2 {
		t.Fatalf("stored %d projects, want 2 independent projects", n)
	}
}

// The connectivity check rejects bad credentials at a real LiveKit server.
// Key namespaces remain independent even when two projects use the same server.
func TestCreateProjectChecksLiveKitConnectivity(t *testing.T) {
	f := newFixture(t)
	lk := liveKitServer(t)
	f.addUser("owner@example.com", "org-a", "admin")
	f.addUser("squatter@example.com", "org-b", "admin")
	s := f.service()
	web := f.membersAPIWith(s, scrape.NewVerifier(false))
	owner, squatter := f.signedIn(s, "owner@example.com"), f.signedIn(s, "squatter@example.com")
	const junk = "made-up-secret-for-a-squatted-key-0123"
	for _, step := range []struct {
		name     string
		org      string
		cookie   *http.Cookie
		body     string
		status   int
		code     string
		projects int
	}{
		{"the owner with a wrong secret", "org-a", owner, projectBody("Production", lk, devKey, "not-the-dev-secret"), http.StatusUnprocessableEntity, "livekit_secret_invalid", 0},
		{"a key LiveKit does not know", "org-a", owner, projectBody("Production", lk, "APIunknown", devSecret), http.StatusUnprocessableEntity, "livekit_secret_invalid", 0},
		{"the owner with the right secret", "org-a", owner, projectBody("Production", lk, devKey, devSecret), http.StatusCreated, "", 1},
		{"another organisation squatting the key with a made-up secret", "org-b", squatter, projectBody("Squat", lk, devKey, junk), http.StatusUnprocessableEntity, "livekit_secret_invalid", 1},
		{"the owner's name again, with a made-up secret", "org-a", owner, projectBody("Production", lk, devKey, junk), http.StatusUnprocessableEntity, "livekit_secret_invalid", 1},
		{"a holder of the secret, the key already stored", "org-b", squatter, projectBody("Squat", lk, devKey, devSecret), http.StatusCreated, "", 2},
		{"the owner again, with the right secret", "org-a", owner, projectBody("Production", lk, devKey, devSecret), http.StatusConflict, "project_exists", 2},
		{"a URL with no LiveKit behind it", "org-a", owner, projectBody("Elsewhere", closedURL(t), devKey, devSecret), http.StatusUnprocessableEntity, "livekit_unreachable", 2},
	} {
		t.Run(step.name, func(t *testing.T) {
			status, body := f.send(web, call{method: http.MethodPost, path: "/api/v1/org/" + step.org + "/projects", body: step.body, cookie: step.cookie})
			if status != step.status || errorCode(body) != step.code {
				t.Errorf("= %d %s, want %d %s", status, body, step.status, step.code)
			}
			if n := f.count(`SELECT count(*) FROM projects`); n != step.projects {
				t.Errorf("%d projects stored, want %d", n, step.projects)
			}
		})
	}
	if n := f.count(`SELECT count(*) FROM projects WHERE organisation_id = 'org-b'`); n != 1 {
		t.Errorf("org-b holds %d projects, want 1", n)
	}
	if strings.Contains(f.logs.String(), junk) || strings.Contains(f.logs.String(), "not-the-dev-secret") {
		t.Error("a log line holds a LiveKit secret")
	}
}

// With VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY, the connectivity read goes through the
// address guard: a private, loopback, link-local or metadata address is never dialed, and its answer
// is the same 422 livekit_unreachable as any URL with no LiveKit, so it says nothing about the
// network behind it.
func TestCreateProjectGuardedReadRevealsNoAddress(t *testing.T) {
	f := newFixture(t)
	f.addUser("admin@example.com", "org-a", "admin")
	s := f.service()
	web := f.membersAPIWith(s, scrape.NewVerifier(true))
	admin := f.signedIn(s, "admin@example.com")
	var hits atomic.Int64
	listening := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		hits.Add(1)
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(listening.Close)
	_, port, err := net.SplitHostPort(strings.TrimPrefix(listening.URL, "http://"))
	if err != nil {
		t.Fatal(err)
	}
	for name, target := range map[string]string{
		"a listening loopback server": listening.URL,
		"the same server by name":     "http://localhost:" + port,
		"its ws:// form":              "ws://127.0.0.1:" + port,
		"a closed loopback port":      closedURL(t),
		"a private address":           "http://10.0.0.7:7880",
		"the metadata address":        "http://169.254.169.254",
	} {
		t.Run(name, func(t *testing.T) {
			status, body := f.send(web, call{method: http.MethodPost, path: "/api/v1/org/org-a/projects", body: projectBody("Inside", target, devKey, devSecret), cookie: admin})
			var e struct{ Code, Message string }
			_ = json.Unmarshal(body, &e)
			if status != http.StatusUnprocessableEntity || e.Code != "livekit_unreachable" || e.Message != "could not reach this LiveKit URL" {
				t.Errorf("= %d %s, want 422 livekit_unreachable", status, body)
			}
		})
	}
	if n := hits.Load(); n != 0 {
		t.Errorf("the listening loopback server got %d requests, want 0", n)
	}
	if n := f.count(`SELECT count(*) FROM projects`); n != 0 {
		t.Errorf("%d projects stored, want 0", n)
	}
}
