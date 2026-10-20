package scrape

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

type resourceTestEndpoints struct{ values []store.OpenResourceEndpoint }

func (e resourceTestEndpoints) OpenResourceEndpoints(context.Context, string, string) ([]store.OpenResourceEndpoint, error) {
	return e.values, nil
}

type resourceTestLock struct{ Locker }

func (resourceTestLock) Lock(context.Context, string, time.Duration) (bool, error) { return true, nil }

type resourceTestStore struct {
	samples []store.ResourceSample
	reads   []store.WorkerEndpointRead
	fail    bool
}

func (s *resourceTestStore) ResourceSamples(context.Context, string, string, time.Time, int) ([]store.ResourceSample, error) {
	return s.samples, nil
}

func (s *resourceTestStore) StoreResourceRead(_ context.Context, _ store.LayerRead, reads []store.WorkerEndpointRead, samples []store.ResourceSample) error {
	if s.fail {
		return errors.New("storage unavailable")
	}
	s.reads = append(s.reads, reads...)
	s.samples = append(s.samples, samples...)
	return nil
}

func TestResourceScraperFailureRecoveryAndDurability(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Second)
	var body atomic.Value
	body.Store(resourceFixture(now))
	var status atomic.Int32
	status.Store(http.StatusOK)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(int(status.Load()))
		_, _ = fmt.Fprint(w, body.Load().(string))
	}))
	defer server.Close()
	samples := &resourceTestStore{}
	hints := 0
	scraper := &ResourceScraper{
		Endpoints: resourceTestEndpoints{[]store.OpenResourceEndpoint{{ResourceEndpoint: store.ResourceEndpoint{ID: "re_one", ContainerID: "/docker/worker"}, URL: server.URL}}},
		Samples:   samples, Lock: resourceTestLock{}, Client: NewWorkerClient(false), Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: func() time.Time { return now }, Every: 15 * time.Second, Timeout: time.Second,
		Changed: func(_ context.Context, org, project string) {
			if org != "org" || project != "p" || len(samples.reads) == 0 {
				t.Error("hint scope or durability")
			}
			hints++
		},
	}
	p := store.Project{OrganisationID: "org", ID: "p"}
	read := func() {
		t.Helper()
		if err := scraper.Resources(context.Background(), p); err != nil {
			t.Fatal(err)
		}
	}
	read()
	if len(samples.samples) != 1 || samples.samples[0].CPUUsed != nil {
		t.Fatal("first sample invented CPU rate")
	}
	now = now.Add(15 * time.Second)
	body.Store(strings.Replace(resourceFixture(now), "} 30 ", "} 45 ", 1))
	read()
	if len(samples.samples) != 2 || samples.samples[1].CPUUsed == nil || *samples.samples[1].CPUUsed != 1 {
		t.Fatal("CPU delta not recorded")
	}
	status.Store(http.StatusServiceUnavailable)
	now = now.Add(15 * time.Second)
	read()
	if len(samples.samples) != 2 || samples.reads[2].OK || samples.reads[2].ErrorClass != store.ErrorUnreachable {
		t.Fatal("failure invented a sample")
	}
	status.Store(http.StatusOK)
	now = now.Add(time.Minute)
	body.Store(resourceFixture(now))
	read()
	if len(samples.samples) != 3 || samples.samples[2].CPUUsed != nil || !samples.reads[3].OK {
		t.Fatal("recovery bridged stale gap")
	}
	samples.fail = true
	if err := scraper.Resources(context.Background(), p); err == nil {
		t.Fatal("failed storage accepted")
	}
	if hints != 4 {
		t.Fatalf("hints=%d, expected only four durable reads", hints)
	}
}
