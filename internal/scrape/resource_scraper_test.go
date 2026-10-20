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
	"testing/synctest"
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

// Lookup, HTTP and persistence share one lease budget, even when each operation
// individually finishes within its configured timeout.
type resourceLeaseStore struct {
	resourceTestStore
	t          *testing.T
	leaseEnd   time.Time
	storeDelay time.Duration
}

func (s *resourceLeaseStore) ResourceSamples(context.Context, string, string, time.Time, int) ([]store.ResourceSample, error) {
	time.Sleep(5 * time.Second)
	return nil, nil
}

func (s *resourceLeaseStore) StoreResourceRead(ctx context.Context, read store.LayerRead, reads []store.WorkerEndpointRead, samples []store.ResourceSample) error {
	deadline, ok := ctx.Deadline()
	if !ok || deadline.After(s.leaseEnd) {
		s.t.Error("persistence can outlive collection lease")
	}
	time.Sleep(s.storeDelay)
	if ctx.Err() != nil {
		return ctx.Err()
	}
	return s.resourceTestStore.StoreResourceRead(ctx, read, reads, samples)
}

type resourceRoundTrip func(*http.Request) (*http.Response, error)

func (f resourceRoundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestResourceCollectionSharesLeaseBudget(t *testing.T) {
	for _, delay := range []time.Duration{0, time.Second} {
		t.Run(delay.String(), func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				leaseEnd := time.Now().Add(13500 * time.Millisecond)
				samples := &resourceLeaseStore{t: t, leaseEnd: leaseEnd, storeDelay: delay}
				client := &http.Client{Transport: resourceRoundTrip(func(r *http.Request) (*http.Response, error) {
					deadline, ok := r.Context().Deadline()
					if !ok || deadline.After(leaseEnd) {
						t.Error("HTTP read can outlive collection lease after slow lookup")
					}
					time.Sleep(8 * time.Second)
					return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(resourceFixture(time.Now())))}, nil
				})}
				hints := 0
				scraper := &ResourceScraper{
					Endpoints: resourceTestEndpoints{[]store.OpenResourceEndpoint{{ResourceEndpoint: store.ResourceEndpoint{ID: "re_one", ContainerID: "/docker/worker"}, URL: "http://exporter.test/metrics"}}},
					Samples:   samples, Lock: resourceTestLock{}, Client: client, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: time.Now, Every: 15 * time.Second, Timeout: 10 * time.Second,
					Changed: func(context.Context, string, string) { hints++ },
				}
				err := scraper.Resources(context.Background(), store.Project{OrganisationID: "org", ID: "p"})
				if delay > 0 {
					if !errors.Is(err, context.DeadlineExceeded) || hints != 0 || len(samples.samples) != 0 {
						t.Fatalf("expired lease stored or published: err=%v hints=%d samples=%d", err, hints, len(samples.samples))
					}
					return
				}
				if err != nil {
					t.Fatal(err)
				}
				if hints != 1 || len(samples.samples) != 1 {
					t.Fatal("in-budget collection was not stored and published")
				}
			})
		})
	}
}
