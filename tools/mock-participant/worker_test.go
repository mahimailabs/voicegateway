package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"github.com/livekit/protocol/auth"
	"github.com/livekit/protocol/livekit"
	"google.golang.org/protobuf/proto"
)

// fakeServer is a LiveKit server as far as the worker handshake is concerned.
type fakeServer struct {
	*httptest.Server

	mu       sync.Mutex
	authSeen string
	pathSeen string
	yQuery   string
	received []*livekit.WorkerMessage

	// send is called once the worker has registered, so a test can drive the
	// availability and assignment flow.
	send func(conn *websocket.Conn)
}

func newFakeServer(t *testing.T, send func(conn *websocket.Conn)) *fakeServer {
	t.Helper()
	f := &fakeServer{send: send}
	up := websocket.Upgrader{}
	f.Server = httptest.NewServer(http.HandlerFunc(func(wr http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		f.authSeen = r.Header.Get("Authorization")
		f.pathSeen = r.URL.Path
		f.yQuery = r.URL.RawQuery
		f.mu.Unlock()

		conn, err := up.Upgrade(wr, r, nil)
		if err != nil {
			return
		}
		defer func() { _ = conn.Close() }()

		// Acknowledge the registration the way a server does.
		ack, _ := proto.Marshal(&livekit.ServerMessage{
			Message: &livekit.ServerMessage_Register{
				Register: &livekit.RegisterWorkerResponse{WorkerId: "W_fake"},
			},
		})
		_ = conn.WriteMessage(websocket.BinaryMessage, ack)
		if f.send != nil {
			f.send(conn)
		}
		for {
			_, payload, err := conn.ReadMessage()
			if err != nil {
				return
			}
			var msg livekit.WorkerMessage
			if err := proto.Unmarshal(payload, &msg); err != nil {
				continue
			}
			f.mu.Lock()
			f.received = append(f.received, &msg)
			f.mu.Unlock()
		}
	}))
	t.Cleanup(f.Close)
	return f
}

func (f *fakeServer) messages() []*livekit.WorkerMessage {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]*livekit.WorkerMessage, len(f.received))
	copy(out, f.received)
	return out
}

func (f *fakeServer) header() (auth string, path string, query string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.authSeen, f.pathSeen, f.yQuery
}

func newWorker(f *fakeServer) *Worker {
	return &Worker{
		URL:       f.URL, // httptest hands out http://, which the worker rewrites
		APIKey:    "devkey",
		APISecret: "secret-that-is-long-enough-for-hs256-signing",
		AgentName: "mock-participant",
	}
}

func waitFor(t *testing.T, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatal("timed out waiting for condition")
}

func TestAgentURLRewritesSchemeAndAddsTheAgentPath(t *testing.T) {
	cases := map[string]string{
		"http://localhost:7880":   "ws://localhost:7880/agent?protocol=1",
		"https://example.invalid": "wss://example.invalid/agent?protocol=1",
		"ws://localhost:7880":     "ws://localhost:7880/agent?protocol=1",
		"wss://example.invalid/":  "wss://example.invalid/agent?protocol=1",
	}
	for in, want := range cases {
		got, err := agentURL(in)
		if err != nil {
			t.Fatalf("agentURL(%q): %v", in, err)
		}
		if got != want {
			t.Errorf("agentURL(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestAgentURLRejectsWhatItCannotDial(t *testing.T) {
	for _, in := range []string{"", "   ", "ftp://host"} {
		if _, err := agentURL(in); err == nil {
			t.Errorf("agentURL(%q) accepted a URL it cannot dial", in)
		}
	}
}

func TestTheRegistrationTokenCarriesTheAgentGrant(t *testing.T) {
	// Without VideoGrant.Agent the server refuses the /agent upgrade, and the
	// failure surfaces as a bare 401 that names nothing.
	w := &Worker{APIKey: "devkey", APISecret: "secret-that-is-long-enough-for-hs256"}
	jwt, err := w.token()
	if err != nil {
		t.Fatalf("token: %v", err)
	}
	verifier, err := auth.ParseAPIToken(jwt)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	_, grants, err := verifier.Verify(w.APISecret)
	if err != nil {
		t.Fatalf("verify: %v", err)
	}
	if grants.Video == nil || !grants.Video.Agent {
		t.Fatalf("token does not carry VideoGrant.Agent: %+v", grants.Video)
	}
}

func TestTokenRefusesToMintWithoutCredentials(t *testing.T) {
	if _, err := (&Worker{APIKey: "k"}).token(); err == nil {
		t.Fatal("minted a token with no secret")
	}
	if _, err := (&Worker{APISecret: "s"}).token(); err == nil {
		t.Fatal("minted a token with no key")
	}
}

func TestConnectRegistersOverTheAgentEndpoint(t *testing.T) {
	f := newFakeServer(t, nil)
	w := newWorker(f)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := w.Connect(ctx); err != nil {
		t.Fatalf("connect: %v", err)
	}
	defer func() { _ = w.Close() }()

	waitFor(t, func() bool { return len(f.messages()) > 0 })
	authHeader, path, query := f.header()
	if path != "/agent" {
		t.Errorf("path = %q, want /agent", path)
	}
	if !strings.Contains(query, "protocol=1") {
		t.Errorf("query = %q, want protocol=1", query)
	}
	if !strings.HasPrefix(authHeader, "Bearer ") {
		t.Errorf("Authorization = %q, want a Bearer token", authHeader)
	}
	reg := f.messages()[0].GetRegister()
	if reg == nil {
		t.Fatalf("first message was not a registration: %+v", f.messages()[0])
	}
	if reg.GetAgentName() != "mock-participant" {
		t.Errorf("agent name = %q", reg.GetAgentName())
	}
	if reg.GetType() != livekit.JobType_JT_ROOM {
		t.Errorf("job type = %v, want JT_ROOM", reg.GetType())
	}
}

// sendJob drives one availability request and then an assignment for room.
func sendJob(room string) func(*websocket.Conn) {
	return func(conn *websocket.Conn) {
		job := &livekit.Job{
			Id:   "JOB_1",
			Type: livekit.JobType_JT_ROOM,
			Room: &livekit.Room{Name: room},
		}
		avail, _ := proto.Marshal(&livekit.ServerMessage{
			Message: &livekit.ServerMessage_Availability{
				Availability: &livekit.AvailabilityRequest{Job: job},
			},
		})
		_ = conn.WriteMessage(websocket.BinaryMessage, avail)

		url := "wss://node-a.example.invalid"
		assign, _ := proto.Marshal(&livekit.ServerMessage{
			Message: &livekit.ServerMessage_Assignment{
				Assignment: &livekit.JobAssignment{
					Job: job, Url: &url, Token: "join-token-for-" + room,
				},
			},
		})
		_ = conn.WriteMessage(websocket.BinaryMessage, assign)
	}
}

func runWorker(t *testing.T, w *Worker) context.CancelFunc {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	if err := w.Connect(ctx); err != nil {
		cancel()
		t.Fatalf("connect: %v", err)
	}
	go func() { _ = w.Run(ctx) }()
	t.Cleanup(func() { cancel(); _ = w.Close() })
	return cancel
}

func TestEveryRoomNameIsAccepted(t *testing.T) {
	// THE trap this worker must never fall into. Every prefix written down for
	// this deployment has been wrong at least once: the docs name one, live
	// configs use others. A worker that filters silently declines the jobs it
	// was started for, and it looks like the dispatcher never dispatched.
	rooms := []string{
		"twilio-sura-test-1",
		"agent-a-42",
		"loadtest",
		"call-inbound-99",
		"", // a job with no room name still gets an answer, not a crash
		"Ω-unicode-room",
	}
	for _, room := range rooms {
		f := newFakeServer(t, sendJob(room))
		w := newWorker(f)

		var mu sync.Mutex
		var got []Assignment
		w.OnAssignment = func(_ context.Context, a Assignment) error {
			mu.Lock()
			defer mu.Unlock()
			got = append(got, a)
			return nil
		}
		runWorker(t, w)

		waitFor(t, func() bool {
			mu.Lock()
			defer mu.Unlock()
			return len(got) == 1
		})

		var availability *livekit.AvailabilityResponse
		for _, m := range f.messages() {
			if a := m.GetAvailability(); a != nil {
				availability = a
			}
		}
		if availability == nil {
			t.Fatalf("room %q: worker never answered availability", room)
		}
		if !availability.GetAvailable() {
			t.Errorf("room %q: worker declined the job", room)
		}
		mu.Lock()
		if got[0].RoomName != room {
			t.Errorf("room %q: assignment carried %q", room, got[0].RoomName)
		}
		mu.Unlock()
	}
}

func TestTheAssignmentSuppliesTheRoomAndTheJoinToken(t *testing.T) {
	f := newFakeServer(t, sendJob("loadtest"))
	w := newWorker(f)
	var mu sync.Mutex
	var got Assignment
	var seen bool
	w.OnAssignment = func(_ context.Context, a Assignment) error {
		mu.Lock()
		defer mu.Unlock()
		got, seen = a, true
		return nil
	}
	runWorker(t, w)
	waitFor(t, func() bool { mu.Lock(); defer mu.Unlock(); return seen })

	mu.Lock()
	defer mu.Unlock()
	if got.JobID != "JOB_1" {
		t.Errorf("job id = %q", got.JobID)
	}
	if got.RoomName != "loadtest" {
		t.Errorf("room = %q", got.RoomName)
	}
	// The token is the server's, job-scoped. The worker must not mint its own.
	if got.Token != "join-token-for-loadtest" {
		t.Errorf("token = %q, want the one the assignment carried", got.Token)
	}
	// A server behind a load balancer hands out a node-specific URL.
	if got.URL != "wss://node-a.example.invalid" {
		t.Errorf("url = %q, want the assignment's", got.URL)
	}
}

func TestAnAssignmentWithNoURLFallsBackToTheRegistrationURL(t *testing.T) {
	f := newFakeServer(t, func(conn *websocket.Conn) {
		job := &livekit.Job{Id: "JOB_2", Room: &livekit.Room{Name: "loadtest"}}
		assign, _ := proto.Marshal(&livekit.ServerMessage{
			Message: &livekit.ServerMessage_Assignment{
				Assignment: &livekit.JobAssignment{Job: job, Token: "t"},
			},
		})
		_ = conn.WriteMessage(websocket.BinaryMessage, assign)
	})
	w := newWorker(f)
	var mu sync.Mutex
	var got Assignment
	var seen bool
	w.OnAssignment = func(_ context.Context, a Assignment) error {
		mu.Lock()
		defer mu.Unlock()
		got, seen = a, true
		return nil
	}
	runWorker(t, w)
	waitFor(t, func() bool { mu.Lock(); defer mu.Unlock(); return seen })
	mu.Lock()
	defer mu.Unlock()
	if got.URL != f.URL {
		t.Errorf("url = %q, want the registration url %q", got.URL, f.URL)
	}
}

func TestAnUnparseableFrameDoesNotKillTheWorker(t *testing.T) {
	// A newer server adding a message type must not take a running worker down
	// mid-load-test.
	f := newFakeServer(t, func(conn *websocket.Conn) {
		_ = conn.WriteMessage(websocket.BinaryMessage, []byte{0xff, 0xff, 0xff, 0xff})
		sendJob("loadtest")(conn)
	})
	w := newWorker(f)
	var mu sync.Mutex
	var seen bool
	w.OnAssignment = func(_ context.Context, _ Assignment) error {
		mu.Lock()
		defer mu.Unlock()
		seen = true
		return nil
	}
	runWorker(t, w)
	waitFor(t, func() bool { mu.Lock(); defer mu.Unlock(); return seen })
}

func TestRunBeforeConnectIsAnErrorNotAPanic(t *testing.T) {
	if err := (&Worker{}).Run(context.Background()); err == nil {
		t.Fatal("Run without Connect should error")
	}
}

func TestCloseIsSafeWithoutConnect(t *testing.T) {
	if err := (&Worker{}).Close(); err != nil {
		t.Fatalf("Close on an unconnected worker: %v", err)
	}
}

func TestAnAssignmentWithNoCallbackIsNotAnError(t *testing.T) {
	f := newFakeServer(t, sendJob("loadtest"))
	w := newWorker(f)
	w.OnAssignment = nil
	runWorker(t, w)
	// The worker must stay up and keep answering; give it a beat to prove it.
	waitFor(t, func() bool {
		for _, m := range f.messages() {
			if m.GetAvailability() != nil {
				return true
			}
		}
		return false
	})
}
