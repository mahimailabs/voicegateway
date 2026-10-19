package scrape

import (
	"context"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"code.dny.dev/ssrf"
	lksdk "github.com/livekit/server-sdk-go/v2"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// livekitCalls is every LiveKit API call a read makes, each by name.
var livekitCalls = []struct {
	name string
	call func(context.Context, LiveKit) error
}{
	{"ListRooms", func(ctx context.Context, lk LiveKit) error { _, err := lk.ListRooms(ctx); return err }},
	{"ListParticipants", func(ctx context.Context, lk LiveKit) error { _, err := lk.ListParticipants(ctx, "r"); return err }},
	{"ListDispatch", func(ctx context.Context, lk LiveKit) error { _, err := lk.ListDispatch(ctx, "r"); return err }},
	{"ListSIPInboundTrunk", func(ctx context.Context, lk LiveKit) error { _, err := lk.ListSIPInboundTrunk(ctx); return err }},
	{"ListSIPOutboundTrunk", func(ctx context.Context, lk LiveKit) error { _, err := lk.ListSIPOutboundTrunk(ctx); return err }},
	{"ListSIPDispatchRule", func(ctx context.Context, lk LiveKit) error { _, err := lk.ListSIPDispatchRule(ctx); return err }},
	{"ListPhoneNumbers", func(ctx context.Context, lk LiveKit) error { _, err := lk.ListPhoneNumbers(ctx); return err }},
	{"ListEgress", func(ctx context.Context, lk LiveKit) error { _, err := lk.ListEgress(ctx); return err }},
	{"ListIngress", func(ctx context.Context, lk LiveKit) error { _, err := lk.ListIngress(ctx); return err }},
	{"ListAgents", func(ctx context.Context, lk LiveKit) error { _, err := lk.ListAgents(ctx); return err }},
	{"ListAgentVersions", func(ctx context.Context, lk LiveKit) error { _, err := lk.ListAgentVersions(ctx, "a"); return err }},
}

// closedPort is a loopback port nothing listens on.
func closedPort(t *testing.T) string {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := ln.Addr().String()
	_ = ln.Close()
	return addr
}

// With public-only on, every LiveKit call to an internal address is refused before it connects, and
// stores the class unreachable: the same class a public host that does not answer gets. So an
// address that answers and one that does not cannot be told apart. The Cloud agents client is
// pointed at the project URL by LK_AGENTS_URL, as it would derive agents.<host> for a Cloud URL.
func TestPublicOnlyLiveKitDialRevealsNoReachability(t *testing.T) {
	var hits atomic.Int64
	listening := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		hits.Add(1)
		w.WriteHeader(http.StatusNotFound)
	}))
	defer listening.Close()
	_, port, _ := net.SplitHostPort(listening.Listener.Addr().String())
	dial := NewDial(true)
	for _, target := range []struct{ name, url string }{
		{"listening loopback", listening.URL},
		{"listening, by name", "http://localhost:" + port},
		{"listening, as ws", "ws://127.0.0.1:" + port},
		{"closed loopback port", "http://" + closedPort(t)},
		{"cloud metadata", "http://169.254.169.254"},
		{"private range", "https://10.0.0.7:7880"},
	} {
		t.Run(target.name, func(t *testing.T) {
			t.Setenv("LK_AGENTS_URL", strings.Replace(target.url, "ws://", "http://", 1))
			lk := dial(target.url, "APIkey", "a-test-secret-not-real")
			for _, c := range livekitCalls {
				ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
				err := c.call(ctx, lk)
				cancel()
				if !errors.Is(err, ssrf.ErrProhibitedIP) && !errors.Is(err, errMetadataAddress) {
					t.Errorf("%s: err %v, want the dial refused by the guard", c.name, err)
				}
				if got := classify(err); got != store.ErrorUnreachable {
					t.Errorf("%s: class %q, want %q", c.name, got, store.ErrorUnreachable)
				}
			}
		})
	}
	if n := hits.Load(); n != 0 {
		t.Errorf("the listening server got %d requests, want none", n)
	}
	// Without the guard, the closed port answers the same class: a refused address looks like one
	// where nothing listens.
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	_, err := DialSDK("http://"+closedPort(t), "APIkey", "a-test-secret-not-real").ListRooms(ctx)
	if got := classify(err); got != store.ErrorUnreachable {
		t.Errorf("unguarded closed port: class %q, want %q", got, store.ErrorUnreachable)
	}
}

// Without public-only, the worker keeps the SDK's own clients, region failover included.
func TestSelfHostedLiveKitDialIsTheSDK(t *testing.T) {
	got, ok := NewDial(false)("http://127.0.0.1:7880", "k", "s").(sdkClient)
	if !ok {
		t.Fatal("NewDial(false) is not the SDK client")
	}
	if _, sdk := got.rooms.(*lksdk.RoomServiceClient); !sdk || len(got.agentOptions) != 0 {
		t.Errorf("self-hosted rooms client %T with agent options %v, want the SDK's and none", got.rooms, got.agentOptions)
	}
	guarded := NewDial(true)("http://127.0.0.1:7880", "k", "s").(sdkClient)
	if _, sdk := guarded.rooms.(*lksdk.RoomServiceClient); sdk || len(guarded.agentOptions) != 1 {
		t.Errorf("public-only rooms client %T with %d agent options, want the guarded one and WithHTTPClient", guarded.rooms, len(guarded.agentOptions))
	}
}
