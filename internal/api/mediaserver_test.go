package api_test

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"github.com/livekit/protocol/auth"
	"github.com/livekit/protocol/livekit"
	lksdk "github.com/livekit/server-sdk-go/v2"
	"github.com/redis/go-redis/v9"
	"github.com/testcontainers/testcontainers-go"
	tcclickhouse "github.com/testcontainers/testcontainers-go/modules/clickhouse"
	tcredis "github.com/testcontainers/testcontainers-go/modules/redis"
	"github.com/testcontainers/testcontainers-go/wait"
	"google.golang.org/protobuf/proto"

	"github.com/mahimailabs/voicegateway/internal/api"
	"github.com/mahimailabs/voicegateway/internal/scrape"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/store/chstore"
)

// The same images, tags and digests as deploy/compose.yaml, plus the LiveKit server the spec names.
const (
	clickhouseImage = "clickhouse/clickhouse-server:26.1.12.23-alpine@sha256:369bd68942569e0effdf0aa68b4f844a74d97b73e281cf8012ce90e9865cc411"
	redisImage      = "redis:8.10.2-alpine@sha256:3811787313eba226a2ef38658c6ccb91cd5e110edc89c37767de373120a0e5a0"
	livekitImage    = "livekit/livekit-server:v1.13.3@sha256:483b8b7b5b0654f91f1e8bdc7b46fcd37fd9911612ecf627f97e3185a89825bd"
)

// livekit-server --dev's own key and secret.
const (
	devKey    = "devkey"
	devSecret = "secret"
	org       = "default"
	agentName = "test-agent"
)

var workers atomic.Int64

// every is the scrape interval in these tests; the lock lasts 9/10 of it, so ticks are spaced by it.
const every = 300 * time.Millisecond

type fixture struct {
	agent    atomic.Pointer[lksdk.Room] // the test agent's connection, once it took a job
	layers   *chstore.Store
	rdb      *redis.Client
	url      string
	projects *projects
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	if os.Getenv("CI") == "" {
		if testing.Short() {
			t.Skip("needs Docker; runs in CI")
		}
		testcontainers.SkipIfProviderIsNotHealthy(t)
	}
	ctx := context.Background()

	ch, err := tcclickhouse.Run(ctx, clickhouseImage,
		tcclickhouse.WithDatabase("voicegateway"), tcclickhouse.WithUsername("voicegateway"), tcclickhouse.WithPassword("ch-test"))
	testcontainers.CleanupContainer(t, ch)
	if err != nil {
		t.Fatalf("start clickhouse: %v", err)
	}
	chURL, err := ch.ConnectionString(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := chstore.Migrate(ctx, chURL); err != nil {
		t.Fatal(err)
	}
	layers, err := chstore.Open(chURL)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = layers.Close() })

	rd, err := tcredis.Run(ctx, redisImage)
	testcontainers.CleanupContainer(t, rd)
	if err != nil {
		t.Fatalf("start redis: %v", err)
	}
	rdURL, err := rd.ConnectionString(ctx)
	if err != nil {
		t.Fatal(err)
	}
	ropts, err := redis.ParseURL(rdURL)
	if err != nil {
		t.Fatal(err)
	}
	rdb := redis.NewClient(ropts)
	t.Cleanup(func() { _ = rdb.Close() })

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
	return &fixture{
		layers: layers, rdb: rdb,
		url:      fmt.Sprintf("http://%s:%s", host, port.Port()),
		projects: &projects{secrets: map[string]string{}},
	}
}

// addProject registers a project reading the test server with the given secret.
func (f *fixture) addProject(id, secret string) store.Project {
	return f.projects.add(store.Project{OrganisationID: org, ID: id, Name: id, LiveKitURL: f.url, LiveKitAPIKey: devKey}, secret)
}

func (f *fixture) scraper(dial scrape.Dial) *scrape.Scraper {
	if dial == nil {
		dial = scrape.DialSDK
	}
	return &scrape.Scraper{
		Projects: f.projects, Samples: f.layers, Lock: scrape.RedisLock{Client: f.rdb}, Dial: dial,
		Log: quiet(), Now: now, ID: fmt.Sprintf("worker-%d", workers.Add(1)),
		Every: every, FullEvery: time.Minute, Timeout: 5 * time.Second,
	}
}

// sample runs n ticks spaced by the interval, as the worker's loop does.
func (f *fixture) sample(t *testing.T, s *scrape.Scraper, n int) {
	t.Helper()
	for i := range n {
		if i > 0 {
			time.Sleep(every)
		}
		s.Tick(context.Background())
	}
}

func (f *fixture) get(t *testing.T, project string) (int, []byte) {
	t.Helper()
	srv := httptest.NewServer((&api.API{Projects: f.projects, Layers: f.layers, Log: quiet(), Now: time.Now}).Routes())
	defer srv.Close()
	res, err := http.Get(srv.URL + "/api/v1/org/" + org + "/project/" + project + "/media-server")
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = res.Body.Close() }()
	body, err := io.ReadAll(res.Body)
	if err != nil {
		t.Fatal(err)
	}
	return res.StatusCode, body
}

func (f *fixture) read(t *testing.T, project string) mediaServer {
	t.Helper()
	code, body := f.get(t, project)
	if code != http.StatusOK {
		t.Fatalf("media server answered %d: %s", code, body)
	}
	var ms mediaServer
	if err := json.Unmarshal(body, &ms); err != nil {
		t.Fatal(err)
	}
	return ms
}

func (f *fixture) roomClient() *lksdk.RoomServiceClient {
	return lksdk.NewRoomServiceClient(f.url, devKey, devSecret)
}

func (f *fixture) createRoom(t *testing.T, name string) *livekit.Room {
	t.Helper()
	r, err := f.roomClient().CreateRoom(context.Background(), &livekit.CreateRoomRequest{Name: name})
	if err != nil {
		t.Fatal(err)
	}
	return r
}

// join connects a participant to a room over WebRTC, as a caller would.
func (f *fixture) join(t *testing.T, room, identity string) {
	t.Helper()
	r, err := lksdk.ConnectToRoom(f.wsURL(), lksdk.ConnectInfo{
		APIKey: devKey, APISecret: devSecret, RoomName: room, ParticipantIdentity: identity,
	}, &lksdk.RoomCallback{}, lksdk.WithAutoSubscribe(false))
	if err != nil {
		t.Fatalf("join %s: %v", room, err)
	}
	t.Cleanup(r.Disconnect)
}

func (f *fixture) wsURL() string { return "ws" + strings.TrimPrefix(f.url, "http") }

// runAgent registers a minimal agent worker over LiveKit's worker protocol (the messages LiveKit
// Agents sends): it accepts every job and joins the room with the job's token.
func (f *fixture) runAgent(t *testing.T) {
	t.Helper()
	at := auth.NewAccessToken(devKey, devSecret).SetVideoGrant(&auth.VideoGrant{Agent: true})
	token, err := at.ToJWT()
	if err != nil {
		t.Fatal(err)
	}
	conn, _, err := websocket.DefaultDialer.Dial(f.wsURL()+"/agent", http.Header{"Authorization": {"Bearer " + token}})
	if err != nil {
		t.Fatalf("agent worker connect: %v", err)
	}
	var mu sync.Mutex
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
		Type: livekit.JobType_JT_ROOM, AgentName: agentName, Version: "test",
	}}})
	registered := make(chan struct{})
	done := make(chan struct{})
	t.Cleanup(func() { _ = conn.Close(); <-done })
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
				send(&livekit.WorkerMessage{Message: &livekit.WorkerMessage_Availability{Availability: &livekit.AvailabilityResponse{
					JobId: msg.Availability.GetJob().GetId(), Available: true, ParticipantIdentity: "agent-" + msg.Availability.GetJob().GetId(),
				}}})
			case *livekit.ServerMessage_Assignment:
				url := msg.Assignment.GetUrl()
				if url == "" {
					url = f.wsURL()
				}
				r, err := lksdk.ConnectToRoomWithToken(url, msg.Assignment.GetToken(), &lksdk.RoomCallback{}, lksdk.WithAutoSubscribe(false))
				if err != nil {
					t.Errorf("agent join: %v", err)
					continue
				}
				t.Cleanup(r.Disconnect)
				r.LocalParticipant.SetAttributes(map[string]string{"lk.agent.state": "listening"})
				f.agent.Store(r)
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

// waitFor polls LiveKit itself until cond holds.
func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(20 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", what)
		}
		time.Sleep(100 * time.Millisecond)
	}
}

func TestMediaServerLive(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	f.addProject("p1", devSecret)
	f.runAgent(t)
	room := f.createRoom(t, "call-1")
	dispatches := lksdk.NewAgentDispatchServiceClient(f.url, devKey, devSecret)
	if _, err := dispatches.CreateDispatch(ctx, &livekit.CreateAgentDispatchRequest{Room: "call-1", AgentName: agentName}); err != nil {
		t.Fatal(err)
	}
	f.join(t, "call-1", "caller")
	var lkParts []*livekit.ParticipantInfo
	waitFor(t, "caller and agent in the room", func() bool {
		res, err := f.roomClient().ListParticipants(ctx, &livekit.ListParticipantsRequest{Room: "call-1"})
		lkParts = res.GetParticipants()
		return err == nil && len(lkParts) == 2
	})
	var lkDispatch *livekit.AgentDispatch
	waitFor(t, "the dispatch's job", func() bool {
		res, err := dispatches.ListDispatch(ctx, &livekit.ListAgentDispatchRequest{Room: "call-1"})
		// LiveKit also keeps the room's own dispatch for unnamed agents; ours is the named one.
		for _, d := range res.GetAgentDispatches() {
			if err == nil && d.GetAgentName() == agentName && len(d.GetState().GetJobs()) == 1 {
				lkDispatch = d
				return d.GetState().GetJobs()[0].GetState().GetStartedAt() > 0
			}
		}
		return false
	})
	listed, err := f.roomClient().ListRooms(ctx, &livekit.ListRoomsRequest{Names: []string{"call-1"}})
	if err != nil || len(listed.GetRooms()) != 1 {
		t.Fatalf("list room: %v", err)
	}
	lkRoom := listed.GetRooms()[0]

	// The agent goes listening, thinking, listening; full reads on every tick see each state.
	s := f.scraper(nil)
	s.FullEvery = every
	f.sample(t, s, 2)
	for _, state := range []string{"thinking", "listening"} {
		waitFor(t, "the agent's connection", func() bool { return f.agent.Load() != nil })
		f.agent.Load().LocalParticipant.SetAttributes(map[string]string{"lk.agent.state": state})
		waitFor(t, "agent state "+state, func() bool {
			res, err := f.roomClient().ListParticipants(ctx, &livekit.ListParticipantsRequest{Room: "call-1"})
			for _, p := range res.GetParticipants() {
				if p.GetKind() == livekit.ParticipantInfo_AGENT && p.GetAttributes()["lk.agent.state"] == state {
					return err == nil
				}
			}
			return false
		})
		time.Sleep(every)
		f.sample(t, s, 2)
	}
	ms := f.read(t, "p1")

	if ms.Server != nil {
		t.Errorf("server = %v, want null: ServerInfo is not read", ms.Server)
	}
	got := ms.room(room.GetSid())
	if got == nil {
		t.Fatalf("room %s missing from %d rooms", room.GetSid(), len(ms.Rooms))
	}
	wantCreated := millisOrSeconds(lkRoom.GetCreationTimeMs(), lkRoom.GetCreationTime())
	if !sameTime(got.CreatedAt, wantCreated) {
		t.Errorf("createdAt = %v, want %v", got.CreatedAt, wantCreated)
	}
	if len(got.Participants) != 2 {
		t.Fatalf("participants = %d, want 2", len(got.Participants))
	}
	kinds := map[string]bool{}
	for _, lp := range lkParts {
		p := got.participant(lp.GetSid())
		if p == nil {
			t.Fatalf("participant %s missing", lp.GetSid())
		}
		kinds[p.Kind] = true
		if want := millisOrSeconds(lp.GetJoinedAtMs(), lp.GetJoinedAt()); !sameTime(p.JoinedAt, want) {
			t.Errorf("%s joinedAt = %v, want %v", lp.GetSid(), p.JoinedAt, want)
		}
		var states []string
		for _, s := range p.States {
			states = append(states, s.State)
		}
		want := "[]"
		if lp.GetKind() == livekit.ParticipantInfo_AGENT {
			want = "[listening thinking listening]"
		}
		if fmt.Sprint(states) != want {
			t.Errorf("%s states = %v, want %s", lp.GetSid(), states, want)
		}
	}
	if !kinds["standard"] || !kinds["agent"] {
		t.Errorf("kinds = %v, want standard and agent", kinds)
	}
	if got.Dispatches == nil {
		t.Fatal("dispatches = null, want them read")
	}
	var d *store.Dispatch
	for i, gd := range *got.Dispatches {
		if gd.ID == lkDispatch.GetId() {
			d = &(*got.Dispatches)[i]
		}
	}
	if d == nil || d.AgentName == nil || *d.AgentName != agentName {
		t.Fatalf("dispatches = %+v, want %s for %s", *got.Dispatches, lkDispatch.GetId(), agentName)
	}
	if !sameTime(d.CreatedAt, nanosTime(lkDispatch.GetState().GetCreatedAt())) {
		t.Errorf("dispatch createdAt = %v, want %v", d.CreatedAt, nanosTime(lkDispatch.GetState().GetCreatedAt()))
	}
	if len(d.Jobs) != 1 {
		t.Fatalf("jobs = %d, want 1", len(d.Jobs))
	}
	lkJob := lkDispatch.GetState().GetJobs()[0]
	if d.Jobs[0].ID != lkJob.GetId() || !sameTime(d.Jobs[0].StartedAt, nanosTime(lkJob.GetState().GetStartedAt())) {
		t.Errorf("job = %s started %v, want %s started %v", d.Jobs[0].ID, d.Jobs[0].StartedAt, lkJob.GetId(), nanosTime(lkJob.GetState().GetStartedAt()))
	}
	turn := lkJob.GetRoom().GetTurnPassword()
	if _, body := f.get(t, "p1"); turn == "" || strings.Contains(string(body), turn) {
		t.Error("the read repeats the room's TURN password, or LiveKit sent none to check against")
	}
	if ms.SourceStatus == nil || ms.SourceStatus.ErrorClass != nil || ms.SourceStatus.LastGoodReadAt == nil {
		t.Errorf("sourceStatus = %+v, want a good last read", ms.SourceStatus)
	}
	if len(ms.Windows) != 1 {
		t.Errorf("windows = %+v, want one continuous window", ms.Windows)
	}
}

func TestRoomEndKeepsLastSeen(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	f.addProject("p1", devSecret)
	room := f.createRoom(t, "call-ends")
	s := f.scraper(nil)
	f.sample(t, s, 2)
	reads, err := f.layers.LayerReads(ctx, org, "p1", store.LayerMediaServer, time.Now().Add(-time.Hour))
	if err != nil || len(reads) != 2 {
		t.Fatalf("reads = %d, %v", len(reads), err)
	}
	lastSaw := reads[len(reads)-1].ReadAt

	if _, err := f.roomClient().DeleteRoom(ctx, &livekit.DeleteRoomRequest{Room: "call-ends"}); err != nil {
		t.Fatal(err)
	}
	time.Sleep(every)
	f.sample(t, s, 2)

	ms := f.read(t, "p1")
	got := ms.room(room.GetSid())
	if got == nil {
		t.Fatal("ended room left the read")
	}
	if got.LastSeenAt == nil || !got.LastSeenAt.Equal(lastSaw) {
		t.Errorf("lastSeenAt = %v, want the last read that saw it, %v", got.LastSeenAt, lastSaw)
	}
	if len(ms.Windows) != 1 || ms.Windows[0].Start == nil || ms.Windows[0].End == nil ||
		ms.Windows[0].Start.After(*got.FirstSeenAt) || ms.Windows[0].End.Before(*got.LastSeenAt) {
		t.Errorf("windows = %+v, want one covering %v to %v", ms.Windows, got.FirstSeenAt, got.LastSeenAt)
	}
}

func TestMediaServerSourceUnavailable(t *testing.T) {
	f := newFixture(t)
	f.addProject("never", "wrong-secret")
	good := f.addProject("good", devSecret)
	room := f.createRoom(t, "call-kept")
	s := f.scraper(nil)
	f.sample(t, s, 2)
	reads, err := f.layers.LayerReads(context.Background(), org, "good", store.LayerMediaServer, time.Now().Add(-time.Hour))
	if err != nil || len(reads) != 2 || !reads[1].OK {
		t.Fatalf("good reads = %+v, %v", reads, err)
	}
	lastGood := reads[1].ReadAt

	f.projects.setSecret(good, "wrong-secret")
	time.Sleep(every)
	f.sample(t, s, 4) // past three intervals

	ms := f.read(t, "good")
	if ms.room(room.GetSid()) == nil {
		t.Error("stored room missing after the secret went bad")
	}
	st := ms.SourceStatus
	if st == nil || st.ErrorClass == nil || *st.ErrorClass != store.ErrorUnauthenticated {
		t.Fatalf("sourceStatus = %+v, want errorClass unauthenticated", st)
	}
	if st.LastGoodReadAt == nil || !st.LastGoodReadAt.Equal(lastGood) {
		t.Errorf("lastGoodReadAt = %v, want %v", st.LastGoodReadAt, lastGood)
	}
	if st.LastReadAt == nil || !st.LastReadAt.After(lastGood) {
		t.Errorf("lastReadAt = %v, want after the last good read", st.LastReadAt)
	}

	code, body := f.get(t, "never")
	var e struct{ Code, Message string }
	if err := json.Unmarshal(body, &e); err != nil {
		t.Fatal(err)
	}
	if code != http.StatusServiceUnavailable || e.Code != "source_unavailable" || !strings.Contains(e.Message, "key or secret") {
		t.Errorf("never-read project answered %d %s %q, want 503 source_unavailable naming the key or secret", code, e.Code, e.Message)
	}
	if code, _ := f.get(t, "missing"); code != http.StatusNotFound {
		t.Errorf("unknown project answered %d, want 404", code)
	}
}

func TestOneScraperPerLayer(t *testing.T) {
	f := newFixture(t)
	f.addProject("p1", devSecret)
	var calls atomic.Int64
	counting := func(url, key, secret string) scrape.LiveKit {
		return countingLiveKit{LiveKit: scrape.DialSDK(url, key, secret), calls: &calls}
	}
	a, b := f.scraper(counting), f.scraper(counting)
	const ticks = 4
	for i := range ticks {
		if i > 0 {
			time.Sleep(every)
		}
		var wg sync.WaitGroup
		wg.Go(func() { a.Tick(context.Background()) })
		wg.Go(func() { b.Tick(context.Background()) })
		wg.Wait()
	}
	if got := calls.Load(); got != ticks {
		t.Errorf("ListRooms ran %d times across two workers, want %d (one per interval)", got, ticks)
	}
	reads, err := f.layers.LayerReads(context.Background(), org, "p1", store.LayerMediaServer, time.Now().Add(-time.Hour))
	if err != nil || len(reads) != ticks {
		t.Errorf("stored reads = %d, %v; want %d", len(reads), err, ticks)
	}
}

type countingLiveKit struct {
	scrape.LiveKit
	calls *atomic.Int64
}

func (c countingLiveKit) ListRooms(ctx context.Context) ([]*livekit.Room, error) {
	c.calls.Add(1)
	return c.LiveKit.ListRooms(ctx)
}

// projects is the project list the worker and web would read from Postgres.
type projects struct {
	mu      sync.Mutex
	list    []store.Project
	secrets map[string]string
}

func (p *projects) add(pr store.Project, secret string) store.Project {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.list = append(p.list, pr)
	p.secrets[pr.ID] = secret
	return pr
}

func (p *projects) setSecret(pr store.Project, secret string) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.secrets[pr.ID] = secret
}

func (p *projects) Projects(context.Context) ([]store.Project, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]store.Project(nil), p.list...), nil
}

func (p *projects) Project(_ context.Context, organisationID, projectID string) (store.Project, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	for _, pr := range p.list {
		if pr.OrganisationID == organisationID && pr.ID == projectID {
			return pr, nil
		}
	}
	return store.Project{}, store.ErrNotFound
}

func (p *projects) LiveKitSecret(_ context.Context, _, projectID string) (string, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.secrets[projectID], nil
}

// mediaServer is the contract's MediaServer, as much of it as these tests read.
type mediaServer struct {
	Server  *json.RawMessage `json:"server"`
	Rooms   []mediaRoom      `json:"rooms"`
	Windows []struct {
		Start, End *time.Time
	} `json:"windows"`
	SourceStatus *struct {
		LastReadAt     *time.Time `json:"lastReadAt"`
		LastGoodReadAt *time.Time `json:"lastGoodReadAt"`
		ErrorClass     *string    `json:"errorClass"`
	} `json:"sourceStatus"`
}

type mediaRoom struct {
	store.Room
	FirstSeenAt  *time.Time `json:"firstSeenAt"`
	LastSeenAt   *time.Time `json:"lastSeenAt"`
	Participants []struct {
		store.RoomParticipant
		States []struct {
			At    *time.Time `json:"at"`
			State string     `json:"state"`
		} `json:"states"`
	} `json:"participants"`
}

func (m mediaServer) room(sid string) *mediaRoom {
	for i := range m.Rooms {
		if m.Rooms[i].SID == sid {
			return &m.Rooms[i]
		}
	}
	return nil
}

func (r *mediaRoom) participant(sid string) *struct {
	store.RoomParticipant
	States []struct {
		At    *time.Time `json:"at"`
		State string     `json:"state"`
	} `json:"states"`
} {
	for i := range r.Participants {
		if r.Participants[i].SID == sid {
			return &r.Participants[i]
		}
	}
	return nil
}

func millisOrSeconds(ms, s int64) *time.Time {
	switch {
	case ms > 0:
		t := time.UnixMilli(ms)
		return &t
	case s > 0:
		t := time.Unix(s, 0)
		return &t
	}
	return nil
}

func nanosTime(n int64) *time.Time {
	if n <= 0 {
		return nil
	}
	t := time.Unix(0, n)
	return &t
}

func sameTime(a, b *time.Time) bool {
	if a == nil || b == nil {
		return a == b
	}
	return a.Equal(*b)
}

// now is the worker's clock at ClickHouse's millisecond precision, so a stored time reads back equal.
func now() time.Time { return time.Now().Truncate(time.Millisecond) }

func quiet() *slog.Logger { return slog.New(slog.NewTextHandler(io.Discard, nil)) }
