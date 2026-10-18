package api_test

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"testing/synctest"
	"time"

	"github.com/livekit/protocol/auth"
	"github.com/livekit/protocol/livekit"
	"github.com/twitchtv/twirp"
	"google.golang.org/protobuf/types/known/timestamppb"

	"github.com/mahimailabs/voicegateway/internal/scrape"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// endpoints is the worker metrics endpoint list web and the worker would read from Postgres
// (pgstore's own tests cover the sealed table).
type endpoints struct {
	mu   sync.Mutex
	list []store.OpenWorkerEndpoint
}

func (e *endpoints) add(project, id, url string) {
	e.mu.Lock()
	defer e.mu.Unlock()
	host := strings.Join(strings.SplitN(url, "/", 4)[:3], "/")
	e.list = append(e.list, store.OpenWorkerEndpoint{
		WorkerEndpoint: store.WorkerEndpoint{OrganisationID: org, ProjectID: project, ID: id, Host: host, CreatedAt: time.Now()},
		URL:            url,
	})
}

func (e *endpoints) OpenWorkerEndpoints(_ context.Context, organisationID, projectID string) ([]store.OpenWorkerEndpoint, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	var out []store.OpenWorkerEndpoint
	for _, ep := range e.list {
		if ep.OrganisationID == organisationID && ep.ProjectID == projectID {
			out = append(out, ep)
		}
	}
	return out, nil
}

func (e *endpoints) WorkerEndpoints(ctx context.Context, organisationID, projectID string) ([]store.WorkerEndpoint, error) {
	open, err := e.OpenWorkerEndpoints(ctx, organisationID, projectID)
	out := make([]store.WorkerEndpoint, 0, len(open))
	for _, ep := range open {
		out = append(out, ep.WorkerEndpoint)
	}
	return out, err
}

// metricsWorker serves what one LiveKit Agents worker's Prometheus endpoint serves: the two gauges
// of telemetry/metrics.py for its nodename, in the classic text format, with a new load and job
// count on every scrape. served records each answer.
type metricsWorker struct {
	nodename string
	mu       sync.Mutex
	served   [][2]float64
	*httptest.Server
}

func newMetricsWorker(t *testing.T, nodename string) *metricsWorker {
	t.Helper()
	w := &metricsWorker{nodename: nodename}
	w.Server = httptest.NewServer(http.HandlerFunc(func(rw http.ResponseWriter, _ *http.Request) {
		w.mu.Lock()
		n := len(w.served)
		load, jobs := 0.05*float64(n+1), float64(n%3)
		w.served = append(w.served, [2]float64{load, jobs})
		w.mu.Unlock()
		rw.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
		_, _ = fmt.Fprintf(rw, "# HELP lk_agents_worker_load Worker load percentage\n# TYPE lk_agents_worker_load gauge\n"+
			"lk_agents_worker_load{nodename=%q} %g\n"+
			"# HELP lk_agents_active_job_count Active jobs\n# TYPE lk_agents_active_job_count gauge\n"+
			"lk_agents_active_job_count{nodename=%q} %g\n"+
			"# TYPE lk_agents_child_process_count gauge\nlk_agents_child_process_count{nodename=%q} 2\n",
			w.nodename, load, w.nodename, jobs, w.nodename)
	}))
	t.Cleanup(w.Close)
	return w
}

func (w *metricsWorker) answers() [][2]float64 {
	w.mu.Lock()
	defer w.mu.Unlock()
	return append([][2]float64(nil), w.served...)
}

// bubbleLock is a Locker on the bubble's clock: Redis keeps its lock TTL in real time, which a
// synctest bubble's fake intervals outrun. TestOneScraperPerLayer covers the Redis lock itself.
type bubbleLock struct {
	mu    sync.Mutex
	until map[string]time.Time
}

func (l *bubbleLock) Lock(_ context.Context, key string, ttl time.Duration) (bool, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.until == nil {
		l.until = map[string]time.Time{}
	}
	if time.Now().Before(l.until[key]) {
		return false, nil
	}
	l.until[key] = time.Now().Add(ttl)
	return true, nil
}

func (l *bubbleLock) Swap(context.Context, string, string, time.Duration) (string, error) {
	return "", nil
}

// sampleWorkers runs the worker's workers loop at its production interval, 15 s, for d on a
// synctest clock. Stored times are the real time the run started plus the bubble's elapsed time, so
// ClickHouse's TTL and the read's one-hour window see ordinary times.
func (f *fixture) sampleWorkers(t *testing.T, from time.Time, d time.Duration) {
	t.Helper()
	synctest.Test(t, func(t *testing.T) {
		start := time.Now()
		s := &scrape.WorkerScraper{
			Projects: f.projects, Endpoints: f.endpoints, Samples: f.layers, Lock: &bubbleLock{},
			// No keep-alive, so no connection outlives a read inside the bubble.
			Client: &http.Client{Transport: &http.Transport{DisableKeepAlives: true}},
			Log:    quiet(),
			Now:    func() time.Time { return from.Add(time.Since(start)).Truncate(time.Millisecond) },
			Every:  15 * time.Second, Timeout: 5 * time.Second,
		}
		ctx, cancel := context.WithCancel(context.Background())
		done := make(chan error, 1)
		go func() { done <- s.Run(ctx) }()
		time.Sleep(d)
		cancel()
		if err := <-done; err != nil {
			t.Error(err)
		}
	})
}

// workersRead is the contract's Workers, as much of it as these tests read.
type workersRead struct {
	Source    string       `json:"source"`
	Workers   []workerRead `json:"workers"`
	Endpoints []struct {
		ID           string           `json:"id"`
		SourceStatus sourceStatusRead `json:"sourceStatus"`
	} `json:"endpoints"`
	SourceStatus sourceStatusRead `json:"sourceStatus"`
}

type workerRead struct {
	ID            string   `json:"id"`
	Nodename      string   `json:"nodename"`
	LoadThreshold *float64 `json:"loadThreshold"`
	Host          string   `json:"host"`
	EndpointID    string   `json:"endpointId"`
	Samples       []struct {
		T          time.Time `json:"t"`
		Load       *float64  `json:"load"`
		ActiveJobs *int64    `json:"activeJobs"`
	} `json:"samples"`
	SourceStatus sourceStatusRead `json:"sourceStatus"`
}

// worker finds a worker by nodename; an absent one reads as empty.
func (w workersRead) worker(nodename string) workerRead {
	for _, wr := range w.Workers {
		if wr.Nodename == nodename {
			return wr
		}
	}
	return workerRead{}
}

type sourceStatusRead struct {
	LastReadAt     *time.Time `json:"lastReadAt"`
	LastGoodReadAt *time.Time `json:"lastGoodReadAt"`
	ErrorClass     *string    `json:"errorClass"`
}

func (f *fixture) readWorkers(t *testing.T, project string) workersRead {
	t.Helper()
	code, body := f.getValid(t, project, "workers")
	if code != http.StatusOK {
		t.Fatalf("workers answered %d: %s", code, body)
	}
	var out workersRead
	if err := json.Unmarshal(body, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

// Acceptance check 10.
func TestWorkersFromMetrics(t *testing.T) {
	f := newFixture(t)
	f.addProject("p1", devSecret)
	a, b := newMetricsWorker(t, "worker-a"), newMetricsWorker(t, "worker-b")
	f.endpoints.add("p1", "we_aaaaaaaaaaaa", a.URL+"/metrics")
	f.endpoints.add("p1", "we_bbbbbbbbbbbb", b.URL+"/metrics")

	if code, body := f.getValid(t, "p1", "workers"); code != http.StatusServiceUnavailable {
		t.Fatalf("listed endpoints never read answered %d: %s", code, body)
	}

	// 60 s at 15 s: reads at 0, 15, 30, 45 and 60 s.
	from := time.Now().Truncate(time.Millisecond)
	f.sampleWorkers(t, from, 60*time.Second+time.Second)
	got := f.readWorkers(t, "p1")
	if len(got.Workers) != 2 {
		t.Fatalf("workers = %+v, want worker-a and worker-b", got.Workers)
	}
	for i, w := range []*metricsWorker{a, b} {
		gw := got.worker(w.nodename)
		served := w.answers()
		if gw.Nodename != w.nodename || gw.LoadThreshold != nil || gw.SourceStatus.ErrorClass != nil || len(served) != 5 {
			t.Fatalf("worker %d = %s threshold %v status %+v, %d scrapes; want %s, null threshold, good reads, 5 scrapes",
				i, gw.Nodename, gw.LoadThreshold, gw.SourceStatus, len(served), w.nodename)
		}
		if len(gw.Samples) != len(served) {
			t.Fatalf("%s samples = %d, want %d", gw.Nodename, len(gw.Samples), len(served))
		}
		for j, s := range gw.Samples {
			if s.Load == nil || *s.Load != served[j][0] || s.ActiveJobs == nil || float64(*s.ActiveJobs) != served[j][1] {
				t.Errorf("%s sample %d = %v/%v, want %v", gw.Nodename, j, s.Load, s.ActiveJobs, served[j])
			}
			if want := from.Add(time.Duration(j) * 15 * time.Second); !s.T.Equal(want) {
				t.Errorf("%s sample %d at %v, want %v", gw.Nodename, j, s.T, want)
			}
		}
	}

	// worker-b's endpoint stops answering: its worker keeps its samples and gains none, and the read
	// names the endpoint unreachable; worker-a goes on.
	lastGood := got.worker("worker-b").Samples[4].T
	b.Close()
	f.sampleWorkers(t, from.Add(75*time.Second), 30*time.Second+time.Second)
	got = f.readWorkers(t, "p1")
	if len(got.Workers) != 2 || len(got.worker("worker-a").Samples) != 8 || len(got.worker("worker-b").Samples) != 5 {
		t.Fatalf("after worker-b stopped: %d workers, want worker-a with 8 samples and worker-b with its 5", len(got.Workers))
	}
	st := got.worker("worker-b").SourceStatus
	if st.ErrorClass == nil || *st.ErrorClass != store.ErrorUnreachable || st.LastGoodReadAt == nil || !st.LastGoodReadAt.Equal(lastGood) {
		t.Errorf("worker-b status = %+v, want unreachable with its last good read at %v", st, lastGood)
	}
	if e := got.Endpoints[1]; e.ID != "we_bbbbbbbbbbbb" || e.SourceStatus.ErrorClass == nil || *e.SourceStatus.ErrorClass != store.ErrorUnreachable {
		t.Errorf("endpoint b = %+v, want unreachable", e)
	}
	if got.worker("worker-a").SourceStatus.ErrorClass != nil || got.SourceStatus.ErrorClass != nil {
		t.Errorf("worker-a %+v and layer %+v statuses, want good", got.worker("worker-a").SourceStatus, got.SourceStatus)
	}
}

// A project that lists no endpoints answers an empty list that says why, and is never read.
func TestWorkersNoneListed(t *testing.T) {
	f := newFixture(t)
	f.addProject("p1", devSecret)
	got := f.readWorkers(t, "p1")
	if len(got.Workers) != 0 || len(got.Endpoints) != 0 || !strings.Contains(got.Source, "lists no worker metrics endpoints") {
		t.Errorf("workers read = %+v, want none with a source saying none are listed", got)
	}
}

// countingDial counts the projects dialled and the Cloud agent calls made.
type countingDial struct {
	dials, cloud atomic.Int64
}

func (c *countingDial) dial(url, key, secret string) scrape.LiveKit {
	c.dials.Add(1)
	return countingCloud{LiveKit: scrape.DialSDK(url, key, secret), calls: &c.cloud}
}

type countingCloud struct {
	scrape.LiveKit
	calls *atomic.Int64
}

func (c countingCloud) ListAgents(ctx context.Context) ([]*livekit.AgentInfo, error) {
	c.calls.Add(1)
	return c.LiveKit.ListAgents(ctx)
}

func (c countingCloud) ListAgentVersions(ctx context.Context, id string) ([]*livekit.AgentVersion, error) {
	c.calls.Add(1)
	return c.LiveKit.ListAgentVersions(ctx, id)
}

func (f *fixture) cloudAgentScraper(dial scrape.Dial) *scrape.CloudAgentScraper {
	return &scrape.CloudAgentScraper{
		Projects: f.projects, Samples: f.layers, Lock: scrape.RedisLock{Client: f.rdb}, Dial: dial,
		Log: quiet(), Now: now, Every: every, Timeout: 5 * time.Second,
	}
}

type cloudAgentsRead struct {
	ReadAt       time.Time          `json:"readAt"`
	Source       string             `json:"source"`
	Agents       []store.CloudAgent `json:"agents"`
	SourceStatus sourceStatusRead   `json:"sourceStatus"`
}

// Acceptance check 11.
func TestCloudAgentsSelfHosted(t *testing.T) {
	f := newFixture(t)
	f.addProject("p1", devSecret)
	var c countingDial
	f.cloudAgentScraper(c.dial).Tick(context.Background())

	code, body := f.getValid(t, "p1", "agents")
	if code != http.StatusOK {
		t.Fatalf("agents answered %d: %s", code, body)
	}
	var got cloudAgentsRead
	if err := json.Unmarshal(body, &got); err != nil {
		t.Fatal(err)
	}
	if got.Agents == nil || len(got.Agents) != 0 || !strings.Contains(got.Source, "LiveKit Cloud agents do not apply") {
		t.Errorf("agents read = %s, want no agents and a source saying Cloud agents do not apply", body)
	}
	if got.ReadAt.IsZero() || got.SourceStatus.ErrorClass != nil {
		t.Errorf("readAt %v, status %+v; want the good read", got.ReadAt, got.SourceStatus)
	}
	if c.dials.Load() != 0 || c.cloud.Load() != 0 {
		t.Errorf("dialled %d times and made %d Cloud agent calls for a self-hosted server, want none", c.dials.Load(), c.cloud.Load())
	}
}

// cloudAgents plays LiveKit Cloud's agents service with LiveKit's own generated Twirp server for the
// CloudAgent service; every other method of it answers unimplemented.
type cloudAgents struct {
	livekit.CloudAgent
	agents   []*livekit.AgentInfo
	versions map[string][]*livekit.AgentVersion
}

func (c cloudAgents) ListAgents(context.Context, *livekit.ListAgentsRequest) (*livekit.ListAgentsResponse, error) {
	return &livekit.ListAgentsResponse{Agents: c.agents}, nil
}

func (c cloudAgents) ListAgentVersions(_ context.Context, req *livekit.ListAgentVersionsRequest) (*livekit.ListAgentVersionsResponse, error) {
	return &livekit.ListAgentVersionsResponse{Versions: c.versions[req.GetAgentId()]}, nil
}

// A LiveKit Cloud project is read through server-sdk-go's AgentClient, with a token signed by the
// project's key that carries the agent admin grant; secrets never reach storage or the read.
func TestCloudAgentsFromCloud(t *testing.T) {
	f := newFixture(t)
	created := time.Date(2026, 10, 12, 22, 48, 24, 0, time.UTC)
	svc := cloudAgents{
		agents: []*livekit.AgentInfo{{
			AgentId: "CA_gLEjzpm2tZLW", AgentName: "assistant", Version: "v2",
			Secrets: []*livekit.AgentSecret{{Name: "OPENAI_API_KEY", Value: []byte("sk-cloud-agent-secret")}},
			AgentDeployments: []*livekit.AgentDeployment{{
				Region: "us-east", ServerRegion: "osbxash1b", Status: "Running", Replicas: 1, MinReplicas: 1, MaxReplicas: 3,
				CpuReq: "1", CurCpu: "250m", MemReq: "1Gi", Version: "v2",
			}},
		}},
		versions: map[string][]*livekit.AgentVersion{"CA_gLEjzpm2tZLW": {
			{Version: "v2", Current: true, Active: true, Status: "Available", CreatedAt: timestamppb.New(created)},
		}},
	}
	var granted atomic.Bool
	handler := livekit.NewCloudAgentServer(svc, twirp.WithServerPathPrefix("/twirp"))
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// LiveKit's own token check: signed by the project's secret with the agent admin grant.
		v, err := auth.ParseAPIToken(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "))
		if err == nil {
			if _, claims, err := v.Verify("cloud-secret"); err == nil && claims.Agent != nil && claims.Agent.Admin {
				granted.Store(true)
				handler.ServeHTTP(w, r)
				return
			}
		}
		http.Error(w, "unauthenticated", http.StatusUnauthorized)
	}))
	t.Cleanup(srv.Close)
	t.Setenv("LK_AGENTS_URL", srv.URL) // server-sdk-go's own override of the Cloud agents host
	f.projects.add(store.Project{OrganisationID: org, ID: "cloud", Name: "cloud", LiveKitURL: "wss://vg-test.livekit.cloud", LiveKitAPIKey: "APIcloud"}, "cloud-secret")

	f.cloudAgentScraper(scrape.DialSDK).Tick(context.Background())
	code, body := f.getValid(t, "cloud", "agents")
	if code != http.StatusOK {
		t.Fatalf("agents answered %d: %s", code, body)
	}
	if !granted.Load() {
		t.Error("no request carried a token signed with the project's secret and the agent admin grant")
	}
	var got cloudAgentsRead
	if err := json.Unmarshal(body, &got); err != nil {
		t.Fatal(err)
	}
	if len(got.Agents) != 1 || got.Agents[0].ID != "CA_gLEjzpm2tZLW" || len(got.Agents[0].Versions) != 1 ||
		got.Agents[0].Versions[0].CreatedAt == nil || !got.Agents[0].Versions[0].CreatedAt.Equal(created) ||
		got.Agents[0].Versions[0].DeployedAt != nil || got.Agents[0].Deployments[0].CurCPU == nil {
		t.Errorf("agents = %s", body)
	}
	if strings.Contains(got.Source, "do not apply") {
		t.Errorf("source = %q for a LiveKit Cloud project", got.Source)
	}
	stored := f.storedRows(t, "cloud_agent_reads", "agents")
	for _, secret := range []string{"sk-cloud-agent-secret", "OPENAI_API_KEY"} {
		if strings.Contains(stored, secret) || strings.Contains(string(body), secret) {
			t.Errorf("%q reached storage or the read", secret)
		}
	}

	// The secret goes bad: the stored read stays, named unauthenticated.
	f.projects.setSecret(store.Project{ID: "cloud"}, "wrong-secret")
	time.Sleep(every)
	f.cloudAgentScraper(scrape.DialSDK).Tick(context.Background())
	if err := json.Unmarshal(mustRead(t, f, "cloud", "agents"), &got); err != nil {
		t.Fatal(err)
	}
	if len(got.Agents) != 1 || got.SourceStatus.ErrorClass == nil || *got.SourceStatus.ErrorClass != store.ErrorUnauthenticated {
		t.Errorf("after the secret went bad: %d agents, status %+v; want the stored agent and unauthenticated", len(got.Agents), got.SourceStatus)
	}
}

func mustRead(t *testing.T, f *fixture, project, read string) []byte {
	t.Helper()
	code, body := f.getValid(t, project, read)
	if code != http.StatusOK {
		t.Fatalf("%s answered %d: %s", read, code, body)
	}
	return body
}
