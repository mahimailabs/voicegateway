package api_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/api"
	"github.com/mahimailabs/voicegateway/internal/scrape"
	"github.com/mahimailabs/voicegateway/internal/store"
)

type nativeResourceEndpoint struct{ endpoint store.OpenResourceEndpoint }

func (e nativeResourceEndpoint) OpenResourceEndpoints(context.Context, string, string) ([]store.OpenResourceEndpoint, error) {
	return []store.OpenResourceEndpoint{e.endpoint}, nil
}

func (e nativeResourceEndpoint) ResourceEndpoints(context.Context, string, string) ([]store.ResourceEndpoint, error) {
	return []store.ResourceEndpoint{e.endpoint.ResourceEndpoint}, nil
}

// This opt-in native check requires a running cAdvisor exporter and a test container
// with 0.5 CPU / 64 MiB limits. Default CI still exercises real storage and HTTP
// failures separately; it does not claim privileged-host exporter verification.
func TestNativeResourceExporterToAPI(t *testing.T) {
	endpointURL, containerID := os.Getenv("VG_TEST_RESOURCE_URL"), os.Getenv("VG_TEST_RESOURCE_CONTAINER")
	if endpointURL == "" || containerID == "" {
		t.Skip("native exporter verification requires VG_TEST_RESOURCE_URL and VG_TEST_RESOURCE_CONTAINER")
	}
	f := newFixture(t)
	p := f.addProject("p1", devSecret)
	endpoints := nativeResourceEndpoint{store.OpenResourceEndpoint{ResourceEndpoint: store.ResourceEndpoint{ID: "re_native", Kind: "worker", Target: "native-test", ContainerID: containerID}, URL: endpointURL}}
	collector := &scrape.ResourceScraper{Endpoints: endpoints, Samples: f.layers, Lock: &bubbleLock{}, Client: scrape.NewWorkerClient(false), Log: quiet(), Now: time.Now, Every: 15 * time.Second, Timeout: 5 * time.Second}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	var latest store.ResourceSample
	for i := 0; i < 6; i++ {
		if err := collector.Resources(ctx, p); err != nil {
			t.Fatal(err)
		}
		samples, err := f.layers.ResourceSamples(ctx, org, p.ID, time.Now().Add(-time.Minute), 100)
		if err != nil || len(samples) == 0 {
			t.Fatal("native sample unavailable", err)
		}
		latest = samples[len(samples)-1]
		if latest.CPUUsed != nil {
			break
		}
		select {
		case <-ctx.Done():
			t.Fatal(ctx.Err())
		case <-time.After(3 * time.Second):
		}
	}
	if latest.CPUUsed == nil || latest.CPUCores == nil || *latest.CPUCores != 0.5 || latest.MemoryBytes == nil || *latest.MemoryBytes <= 0 || latest.MemoryLimitBytes == nil || *latest.MemoryLimitBytes != 67108864 {
		t.Fatalf("native resource mismatch: %+v", latest)
	}
	gate, key := api.KeyFor(org, p.ID)
	service := (&api.API{Projects: f.projects, Gate: gate, ResourceEndpoints: endpoints, ResourceSamples: f.layers, Now: time.Now, Log: quiet()}).Routes()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/org/default/project/p1/resources", nil)
	req.Header.Set("Authorization", "Bearer "+key)
	res := httptest.NewRecorder()
	service.ServeHTTP(res, req)
	if res.Code != 200 {
		t.Fatal(res.Code, res.Body.String())
	}
	var got struct {
		Resources []struct {
			Samples []struct {
				CPUUsed     *float64 `json:"cpuUsed"`
				MemoryBytes *float64 `json:"memoryBytes"`
			} `json:"samples"`
		} `json:"resources"`
	}
	if err := json.Unmarshal(res.Body.Bytes(), &got); err != nil {
		t.Fatal(err)
	}
	if len(got.Resources) != 1 || len(got.Resources[0].Samples) == 0 {
		t.Fatal("native snapshot missing")
	}
	sample := got.Resources[0].Samples[len(got.Resources[0].Samples)-1]
	if sample.CPUUsed == nil || sample.MemoryBytes == nil {
		t.Fatal("native measurement lost at API")
	}
	t.Logf("Native exporter -> collector -> ClickHouse -> authenticated API: CPU %.6f cores, allocation %.1f cores, memory %.0f / %.0f bytes", *sample.CPUUsed, *latest.CPUCores, *sample.MemoryBytes, *latest.MemoryLimitBytes)
}
