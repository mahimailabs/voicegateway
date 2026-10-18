package api_test

import (
	"context"
	"net/http"
	"testing"
	"time"

	"github.com/livekit/protocol/livekit"
	lksdk "github.com/livekit/server-sdk-go/v2"

	"github.com/mahimailabs/voicegateway/internal/scrape"
)

// Acceptance check 12: the six LiveKit layer reads, each served from what the worker read of a real
// livekit-server --dev and a LiveKit Agents metrics endpoint, match their schemas in
// docs/api/openapi.yaml.
func TestReadsMatchContract(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	f.addProject("p1", devSecret)
	f.createRoom(t, "call-contract")
	if _, err := lksdk.NewSIPClient(f.url, devKey, devSecret).CreateSIPInboundTrunk(ctx, &livekit.CreateSIPInboundTrunkRequest{
		Trunk: &livekit.SIPInboundTrunkInfo{Name: "contract", Numbers: []string{"+15550001111"}},
	}); err != nil {
		t.Fatal(err)
	}
	w := newMetricsWorker(t, "worker-contract")
	f.endpoints.add("p1", "we_cccccccccccc", w.URL+"/metrics")

	f.scraper(nil).Tick(ctx)
	f.sipScraper(nil).Tick(ctx)
	f.egressScraper().Tick(ctx)
	f.ingressScraper().Tick(ctx)
	f.cloudAgentScraper(scrape.DialSDK).Tick(ctx)
	(&scrape.WorkerScraper{
		Projects: f.projects, Endpoints: f.endpoints, Samples: f.layers, Lock: scrape.RedisLock{Client: f.rdb},
		Client: scrape.NewWorkerClient(false), Log: quiet(), Now: now, Every: every, Timeout: 5 * time.Second,
	}).Tick(ctx)

	for _, read := range []string{"media-server", "sip", "egress", "ingress", "agents", "workers"} {
		t.Run(read, func(t *testing.T) {
			if code, body := f.getValid(t, "p1", read); code != http.StatusOK {
				t.Errorf("%s answered %d: %s", read, code, body)
			}
		})
	}
}
