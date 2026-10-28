package api_test

import (
	"bufio"
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/livekit/protocol/livekit"
	lksdk "github.com/livekit/server-sdk-go/v2"

	"github.com/mahimailabs/voicegateway/internal/api"
	"github.com/mahimailabs/voicegateway/internal/scrape"
	"github.com/mahimailabs/voicegateway/internal/stream"
)

// Real LiveKit/Prometheus -> durable ClickHouse -> Redis -> authenticated SSE -> API snapshot.
func TestLiveNativeSamplesReachStream(t *testing.T) {
	f := newFixture(t)
	p := f.addProject("p1", devSecret)
	live := stream.NewLive(f.rdb.Options(), quiet())
	defer func() { _ = live.Close() }()
	gate, key := api.KeyFor(org, p.ID)
	server := httptest.NewServer((&api.API{Projects: f.projects, Gate: gate, Live: live, Log: quiet()}).Routes())
	defer server.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, server.URL+"/api/v1/org/default/project/p1/live-events", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+key)
	response, err := server.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = response.Body.Close() }()
	reader := bufio.NewReader(response.Body)
	refresh := func() {
		t.Helper()
		for _, want := range []string{"event: refresh\n", "data: {}\n", "\n"} {
			got, err := reader.ReadString('\n')
			if err != nil || got != want {
				t.Fatalf("frame %q %v", got, err)
			}
		}
	}
	refresh()
	room, err := lksdk.NewRoomServiceClient(f.url, devKey, devSecret).CreateRoom(ctx, &livekit.CreateRoomRequest{Name: "live-feed-proof"})
	if err != nil {
		t.Fatal(err)
	}
	sfus := f.scraper(nil)
	sfus.Changed = live.Notify
	sfus.Tick(ctx)
	refresh()
	media := f.read(t, "p1")
	if len(media.Rooms) != 1 || media.Rooms[0].SID != room.Sid {
		t.Fatalf("notification preceded room storage: %+v", media.Rooms)
	}
	worker := newMetricsWorker(t, "native-worker")
	f.endpoints.add("p1", "we_aaaaaaaaaaaa", worker.URL+"/metrics")
	sampler := &scrape.WorkerScraper{Projects: f.projects, Endpoints: f.endpoints, Samples: f.layers, Lock: &bubbleLock{}, Client: scrape.NewWorkerClient(false), Log: quiet(), Now: time.Now, Every: 15 * time.Second, Timeout: 5 * time.Second, Changed: live.Notify}
	if err := sampler.Workers(ctx, p); err != nil {
		t.Fatal(err)
	}
	refresh()
	got := f.readWorkers(t, "p1")
	if len(got.Workers) != 1 || len(got.Workers[0].Samples) != 1 {
		t.Fatalf("notification preceded worker storage: %+v", got.Workers)
	}
	// Manually trigger a second collection without waiting for the scheduling lease.
	worker.Close()
	sampler.Lock = &bubbleLock{}
	if err := sampler.Workers(ctx, p); err != nil {
		t.Fatal(err)
	}
	refresh()
	unavailable := f.readWorkers(t, "p1")
	if unavailable.SourceStatus.ErrorClass == nil || len(unavailable.Workers) != 1 || len(unavailable.Workers[0].Samples) != 1 {
		t.Fatalf("failed source must notify and retain timestamped prior samples: %+v", unavailable)
	}
}
