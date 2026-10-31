package capacity

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"github.com/livekit/protocol/auth"
	"github.com/livekit/protocol/livekit"
	lksdk "github.com/livekit/server-sdk-go/v2"
	"github.com/moby/moby/api/types/container"
	"github.com/pion/webrtc/v4"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"
	"google.golang.org/protobuf/proto"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// The LiveKit server the other packages' tests run, by tag and digest; --dev gives it devkey and
// secret.
const (
	livekitImage = "livekit/livekit-server:v1.13.3@sha256:483b8b7b5b0654f91f1e8bdc7b46fcd37fd9911612ecf627f97e3185a89825bd"
	devKey       = "devkey"
	devSecret    = "secret"
	testAgent    = "capacity-agent"
)

// liveKit starts livekit-server --dev on the host's network, so the callers' and the test agent's
// audio reach it over WebRTC, and returns its URL.
func liveKitServer(t *testing.T) string {
	t.Helper()
	if os.Getenv("CI") == "" {
		if testing.Short() {
			t.Skip("needs Docker; runs in CI")
		}
		testcontainers.SkipIfProviderIsNotHealthy(t)
	}
	ports := freePorts(t, 2)
	body := fmt.Sprintf("port: %d\nrtc:\n  tcp_port: %d\n  port_range_start: 50000\n  port_range_end: 50100\n  use_external_ip: false\n", ports[0], ports[1])
	lk, err := testcontainers.GenericContainer(t.Context(), testcontainers.GenericContainerRequest{
		ContainerRequest: testcontainers.ContainerRequest{
			Image:              livekitImage,
			Cmd:                []string{"--dev", "--bind", "127.0.0.1", "--node-ip", "127.0.0.1", "--config-body", body},
			HostConfigModifier: func(hc *container.HostConfig) { hc.NetworkMode = "host" },
			WaitingFor:         wait.ForLog("starting LiveKit server").WithStartupTimeout(time.Minute),
		},
		Started: true,
	})
	testcontainers.CleanupContainer(t, lk)
	if err != nil {
		t.Fatalf("start livekit-server: %v", err)
	}
	url := fmt.Sprintf("http://127.0.0.1:%d", ports[0])
	deadline := time.Now().Add(30 * time.Second)
	for {
		res, err := http.Get(url) //nolint:gosec // the test's own server
		if err == nil {
			_ = res.Body.Close()
			return url
		}
		if time.Now().After(deadline) {
			t.Fatalf("livekit-server never answered: %v", err)
		}
		time.Sleep(200 * time.Millisecond)
	}
}

func freePorts(t *testing.T, n int) []int {
	t.Helper()
	var out []int
	for range n {
		l, err := net.Listen("tcp", "127.0.0.1:0")
		if err != nil {
			t.Fatal(err)
		}
		out = append(out, l.Addr().(*net.TCPAddr).Port)
		defer func() { _ = l.Close() }()
	}
	return out
}

// testAgentWorker registers a worker for testAgent over LiveKit's agent worker protocol and takes at
// most jobs calls at once. In each call it greets, then, each time the caller's speech stops, thinks
// for a moment and speaks, keeping lk.agent.state as LiveKit Agents does.
func testAgentWorker(t *testing.T, url string, jobs int) {
	t.Helper()
	token, err := auth.NewAccessToken(devKey, devSecret).SetVideoGrant(&auth.VideoGrant{Agent: true}).ToJWT()
	if err != nil {
		t.Fatal(err)
	}
	wsURL := "ws" + strings.TrimPrefix(url, "http")
	conn, _, err := websocket.DefaultDialer.Dial(wsURL+"/agent", http.Header{"Authorization": {"Bearer " + token}})
	if err != nil {
		t.Fatalf("agent worker connect: %v", err)
	}
	var (
		mu     sync.Mutex
		active int
		rooms  []*lksdk.Room
	)
	send := func(m *livekit.WorkerMessage) {
		b, err := proto.Marshal(m)
		if err != nil {
			t.Error(err)
			return
		}
		mu.Lock()
		defer mu.Unlock()
		_ = conn.WriteMessage(websocket.BinaryMessage, b)
	}
	send(&livekit.WorkerMessage{Message: &livekit.WorkerMessage_Register{Register: &livekit.RegisterWorkerRequest{
		Type: livekit.JobType_JT_ROOM, AgentName: testAgent, Version: "test",
	}}})
	registered := make(chan struct{})
	done := make(chan struct{})
	t.Cleanup(func() {
		_ = conn.Close()
		<-done
		mu.Lock()
		defer mu.Unlock()
		for _, r := range rooms {
			r.Disconnect()
		}
	})
	go func() {
		defer close(done)
		var once sync.Once
		for {
			_, b, err := conn.ReadMessage()
			if err != nil {
				return
			}
			var m livekit.ServerMessage
			if err := proto.Unmarshal(b, &m); err != nil {
				continue
			}
			switch msg := m.Message.(type) {
			case *livekit.ServerMessage_Register:
				once.Do(func() { close(registered) })
			case *livekit.ServerMessage_Availability:
				mu.Lock()
				ok := active < jobs
				if ok {
					active++
				}
				mu.Unlock()
				job := msg.Availability.GetJob().GetId()
				send(&livekit.WorkerMessage{Message: &livekit.WorkerMessage_Availability{Availability: &livekit.AvailabilityResponse{
					JobId: job, Available: ok, ParticipantIdentity: "agent-" + job,
				}}})
			case *livekit.ServerMessage_Assignment:
				r := answeringAgent(t, wsURL, msg.Assignment.GetToken())
				if r == nil {
					continue
				}
				mu.Lock()
				rooms = append(rooms, r)
				mu.Unlock()
				send(&livekit.WorkerMessage{Message: &livekit.WorkerMessage_UpdateJob{UpdateJob: &livekit.UpdateJobStatus{
					JobId: msg.Assignment.GetJob().GetId(), Status: livekit.JobStatus_JS_RUNNING,
				}}})
			}
		}
	}()
	select {
	case <-registered:
	case <-time.After(10 * time.Second):
		t.Fatal("agent worker never registered")
	}
}

// answeringAgent joins a job's room as the agent and answers the caller's speech by state alone.
func answeringAgent(t *testing.T, wsURL, token string) *lksdk.Room {
	var r *lksdk.Room
	setState := func(s string) { r.LocalParticipant.SetAttributes(map[string]string{agentState: s}) }
	heard := make(chan struct{}, 1)
	cb := &lksdk.RoomCallback{ParticipantCallback: lksdk.ParticipantCallback{
		OnTrackSubscribed: func(track *webrtc.TrackRemote, _ *lksdk.RemoteTrackPublication, _ *lksdk.RemoteParticipant) {
			go func() {
				for {
					p, _, err := track.ReadRTP()
					if err != nil {
						return
					}
					// The caller's silence frames are 3 bytes; its speech is larger.
					if len(p.Payload) > len(silence) {
						select {
						case heard <- struct{}{}:
						default:
						}
					}
				}
			}()
		},
	}}
	r, err := lksdk.ConnectToRoomWithToken(wsURL, token, cb)
	if err != nil {
		t.Errorf("agent join: %v", err)
		return nil
	}
	go func() {
		setState("speaking") // the greeting
		time.Sleep(time.Second)
		setState("listening")
		quiet := time.NewTimer(time.Hour)
		speaking := false
		for {
			select {
			case _, ok := <-heard:
				if !ok {
					return
				}
				speaking = true
				quiet.Reset(300 * time.Millisecond)
			case <-quiet.C:
				if !speaking {
					continue
				}
				speaking = false
				setState("thinking")
				time.Sleep(200 * time.Millisecond)
				setState("speaking")
				time.Sleep(time.Second)
				setState("listening")
			}
			if r.ConnectionState() == lksdk.ConnectionStateDisconnected {
				return
			}
		}
	}()
	return r
}

type liveProjects struct{ url string }

func (p liveProjects) Project(_ context.Context, org, id string) (store.Project, error) {
	return store.Project{OrganisationID: org, ID: id, LiveKitURL: p.url, LiveKitAPIKey: devKey}, nil
}

func (liveProjects) LiveKitSecret(context.Context, string, string) (string, error) {
	return devSecret, nil
}

func (liveProjects) ProjectSettings(context.Context, string, string) (store.ProjectSettings, error) {
	return store.ProjectSettings{Thresholds: store.Thresholds{JoinMs: 2000}}, nil
}

// TestRunStopsWhenAJobStaysPending runs the real runner, caller and LiveKit clients against
// livekit-server --dev and a test agent that takes 4 jobs: steps 1, 2 and 4 pass with every call
// answered, 8 stops on its pending jobs, and no test room is left.
func TestRunStopsWhenAJobStaysPending(t *testing.T) {
	url := liveKitServer(t)
	testAgentWorker(t, url, 4)
	runs := &fakeRuns{steps: map[int]store.CapacityStep{}, run: store.CapacityRun{
		ID: "5f0c2a4e-9b7d-4c1e-8a3f-2d6b1e0c9a77", OrganisationID: "o", ProjectID: "p", Agent: testAgent, MaxStep: 16, State: store.CapacityRunning,
	}}
	r := &Runner{
		Runs: runs, Projects: liveProjects{url}, Dial: NewDial(http.DefaultClient),
		CPU: func() (float64, error) { return 10, nil }, ID: "worker-1",
		Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: time.Now,
		Poll: time.Second, Beat: time.Second, Lost: 30 * time.Second, Unclaimed: time.Minute,
		StepFor: 15 * time.Second, BusyEvery: 5 * time.Second, Timeout: 10 * time.Second,
	}
	if err := r.Tick(t.Context()); err != nil {
		t.Fatal(err)
	}
	if runs.end == nil || runs.end.LimitedBy != store.LimitPending || capacityOf(runs.end) != 4 {
		t.Fatalf("end = %+v, want pending with capacity 4", runs.end)
	}
	for _, n := range []int{1, 2, 4} {
		s := runs.steps[n]
		if s.Outcome != store.StepPassed || s.Joined != n || len(s.AnswerSeconds) < n || s.NoAnswer != 0 {
			t.Errorf("step %d = %s, joined %d, %d answers, %d unanswered; want passed with every call joined and answered", n, s.Outcome, s.Joined, len(s.AnswerSeconds), s.NoAnswer)
		}
		for _, a := range s.AnswerSeconds {
			// The agent answers 200 ms after 300 ms of the caller's silence.
			if a < 0.4 || a > 2 {
				t.Errorf("step %d answer %.2f s, want about 0.5 s", n, a)
			}
		}
	}
	if s := runs.steps[8]; s.Outcome != store.StepFailed || s.Joined != 4 || s.Pending != 4 {
		t.Errorf("step 8 = %s, joined %d, pending %d; want failed with 4 joined and 4 pending", s.Outcome, s.Joined, s.Pending)
	}
	rooms, err := lksdk.NewRoomServiceClient(url, devKey, devSecret).ListRooms(t.Context(), &livekit.ListRoomsRequest{})
	if err != nil {
		t.Fatal(err)
	}
	for _, room := range rooms.GetRooms() {
		if IsTestRoom(room.GetName()) {
			t.Errorf("test room %s left open", room.GetName())
		}
	}
}

// TestBusyAgentIsRefusedOnLiveKit: a room the runner did not create with a job for the agent makes
// the run end as agent_busy before it creates any room.
func TestBusyAgentIsRefusedOnLiveKit(t *testing.T) {
	url := liveKitServer(t)
	testAgentWorker(t, url, 4)
	ctx := t.Context()
	if _, err := lksdk.NewRoomServiceClient(url, devKey, devSecret).CreateRoom(ctx, &livekit.CreateRoomRequest{Name: "customer-call"}); err != nil {
		t.Fatal(err)
	}
	if _, err := lksdk.NewAgentDispatchServiceClient(url, devKey, devSecret).CreateDispatch(ctx, &livekit.CreateAgentDispatchRequest{Room: "customer-call", AgentName: testAgent}); err != nil {
		t.Fatal(err)
	}
	lk := NewDial(http.DefaultClient)(url, devKey, devSecret)
	deadline := time.Now().Add(10 * time.Second)
	for {
		busy, err := lk.AgentRooms(ctx, testAgent)
		if err != nil {
			t.Fatal(err)
		}
		if len(busy) == 1 && busy[0] == "customer-call" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("AgentRooms = %v, want [customer-call]", busy)
		}
		time.Sleep(200 * time.Millisecond)
	}
	if busy, err := lk.AgentRooms(ctx, "another-agent"); err != nil || len(busy) != 0 {
		t.Errorf("AgentRooms(another-agent) = %v, %v; want none", busy, err)
	}
}

type listRooms struct {
	livekit.RoomService
	rooms []string
	// ended are rooms that end between the first list and the dispatch read.
	ended map[string]bool
}

func (l listRooms) ListRooms(_ context.Context, req *livekit.ListRoomsRequest) (*livekit.ListRoomsResponse, error) {
	res := &livekit.ListRoomsResponse{}
	for _, name := range l.rooms {
		if len(req.GetNames()) > 0 && (l.ended[name] || req.GetNames()[0] != name) {
			continue
		}
		res.Rooms = append(res.Rooms, &livekit.Room{Name: name})
	}
	return res, nil
}

type failingDispatches struct{ livekit.AgentDispatchService }

func (failingDispatches) ListDispatch(context.Context, *livekit.ListAgentDispatchRequest) (*livekit.ListAgentDispatchResponse, error) {
	return nil, fmt.Errorf("twirp error not_found: room not found")
}

// TestAgentRoomsSkipsAnEndedRoom: a call that ends between the room list and its dispatch read is
// not busy and not an error; a room still there whose dispatches cannot be read is an error.
func TestAgentRoomsSkipsAnEndedRoom(t *testing.T) {
	for name, tt := range map[string]struct {
		ended   bool
		wantErr bool
	}{
		"ended":       {ended: true},
		"still there": {wantErr: true},
	} {
		t.Run(name, func(t *testing.T) {
			l := &liveKit{
				rooms:      listRooms{rooms: []string{"customer-call"}, ended: map[string]bool{"customer-call": tt.ended}},
				dispatches: failingDispatches{}, apiKey: devKey, apiSecret: devSecret,
			}
			busy, err := l.AgentRooms(t.Context(), testAgent)
			if (err != nil) != tt.wantErr || len(busy) != 0 {
				t.Errorf("AgentRooms = %v, %v; want no rooms and error %v", busy, err, tt.wantErr)
			}
		})
	}
}
