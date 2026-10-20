package scrape

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"github.com/prometheus/common/expfmt"
	"golang.org/x/sync/errgroup"

	"github.com/mahimailabs/voicegateway/internal/store"
)

type ResourceEndpoints interface {
	OpenResourceEndpoints(context.Context, string, string) ([]store.OpenResourceEndpoint, error)
}
type ResourceSamples interface {
	StoreResourceRead(context.Context, store.LayerRead, []store.WorkerEndpointRead, []store.ResourceSample) error
	ResourceSamples(context.Context, string, string, time.Time, int) ([]store.ResourceSample, error)
}

// ResourceScraper reads explicitly configured container exporters using the same
// guarded HTTP client and durable-source lifecycle as native worker metrics.
type ResourceScraper struct {
	Changed   func(context.Context, string, string)
	Projects  Projects
	Endpoints ResourceEndpoints
	Samples   ResourceSamples
	Lock      Locker
	Client    *http.Client
	Log       *slog.Logger
	Now       func() time.Time
	Every     time.Duration
	Timeout   time.Duration
}

// Run reads every project once per Every until ctx is cancelled. It returns nil on cancellation.
func (s *ResourceScraper) Run(ctx context.Context) error {
	t := time.NewTicker(s.Every)
	defer t.Stop()
	for {
		s.Tick(ctx)
		select {
		case <-ctx.Done():
			return nil
		case <-t.C:
		}
	}
}

// Tick reads every project's resource endpoints once.
func (s *ResourceScraper) Tick(ctx context.Context) {
	projects, err := s.Projects.Projects(ctx)
	if err != nil {
		if ctx.Err() == nil {
			s.Log.Error("scrape: list projects", "err", err)
		}
		return
	}
	eachProject(projects, func(p store.Project) {
		if err := s.Resources(ctx, p); err != nil && ctx.Err() == nil {
			s.Log.Error("scrape: resources", "organisation", p.OrganisationID, "project", p.ID, "err", err)
		}
	})
}

// Resources reads one project's listed endpoints and stores what they reported with the read. A
// project that lists none is not read. It skips the project when another worker holds its lock for
// this interval. The layer read is good when at least one endpoint answered; each endpoint's own
// read is stored as well, so one that stops answering is named.
func (s *ResourceScraper) Resources(ctx context.Context, p store.Project) error {
	endpoints, err := s.Endpoints.OpenResourceEndpoints(ctx, p.OrganisationID, p.ID)
	if err != nil {
		return fmt.Errorf("resource endpoints: %w", err)
	}
	if len(endpoints) == 0 {
		return nil
	}
	hold := s.Every * 9 / 10
	leaseEnd := time.Now().Add(hold)
	got, err := s.Lock.Lock(ctx, lockKey(p, store.LayerResources), hold)
	if err != nil {
		return fmt.Errorf("lock: %w", err)
	}
	if !got {
		return nil
	}

	// Include the prior-sample lookup and durable write in the same lease budget.
	rctx, cancel := context.WithDeadline(ctx, leaseEnd)
	defer cancel()
	previousCtx, previousCancel := context.WithTimeout(rctx, s.Timeout)
	previous, err := s.Samples.ResourceSamples(previousCtx, p.OrganisationID, p.ID, s.Now().Add(-45*time.Second), 10000)
	previousCancel()
	if err != nil {
		return fmt.Errorf("read previous resources: %w", err)
	}
	prior := make(map[string]store.ResourceSample)
	for _, sample := range previous {
		if old, ok := prior[sample.EndpointID]; !ok || sample.ReadAt.After(old.ReadAt) {
			prior[sample.EndpointID] = sample
		}
	}
	results := make([]containerResource, len(endpoints))
	classes := make([]string, len(endpoints))
	var g errgroup.Group
	g.SetLimit(endpointReads)
	for i, e := range endpoints {
		g.Go(func() error {
			results[i], classes[i] = s.readResourceEndpoint(rctx, e.URL, e.ContainerID)
			return nil
		})
	}
	_ = g.Wait() // each read reports its own class, never an error
	if ctx.Err() != nil {
		return nil
	}

	readAt := s.Now().UTC()
	read := store.LayerRead{OrganisationID: p.OrganisationID, ProjectID: p.ID, Layer: store.LayerResources, Every: s.Every, ReadAt: readAt}
	reads := make([]store.WorkerEndpointRead, 0, len(endpoints))
	var samples []store.ResourceSample
	for i, e := range endpoints {
		er := store.WorkerEndpointRead{EndpointID: e.ID, ReadAt: readAt, Every: s.Every, OK: classes[i] == "", ErrorClass: classes[i]}
		reads = append(reads, er)
		if !er.OK {
			s.Log.Warn("scrape: resource endpoint read failed", "organisation", p.OrganisationID, "project", p.ID, "endpoint", e.ID, "class", er.ErrorClass)
			if read.ErrorClass == "" {
				read.ErrorClass = er.ErrorClass // the first endpoint's class names the layer's failure
			}
			continue
		}
		read.OK = true
		m := results[i]
		previous := prior[e.ID]
		samples = append(samples, store.ResourceSample{
			OrganisationID: p.OrganisationID, ProjectID: p.ID, EndpointID: e.ID, ReadAt: readAt,
			ObservedAt: m.ObservedAt, StartedAt: m.StartedAt, CPUSeconds: m.CPUSeconds,
			CPUUsed:  containerCPURate(containerResource{ID: e.ContainerID, ObservedAt: previous.ObservedAt, StartedAt: previous.StartedAt, CPUSeconds: previous.CPUSeconds}, m),
			CPUCores: m.CPUCores, MemoryBytes: m.MemoryBytes, MemoryLimitBytes: m.MemoryLimitBytes,
		})
	}
	if read.OK {
		read.ErrorClass = ""
	}
	sctx, cancel := context.WithTimeout(rctx, s.Timeout)
	defer cancel()
	if err := s.Samples.StoreResourceRead(sctx, read, reads, samples); err != nil {
		return fmt.Errorf("store resource read: %w", err)
	}
	if s.Changed != nil {
		s.Changed(ctx, read.OrganisationID, read.ProjectID)
	}
	return nil
}

func (s *ResourceScraper) readResourceEndpoint(ctx context.Context, rawURL, containerID string) (containerResource, string) {
	cctx, cancel := context.WithTimeout(ctx, s.Timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(cctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return containerResource{}, store.ErrorOther
	}
	// Request the classic Prometheus text format exported by cAdvisor.
	req.Header.Set("Accept", string(expfmt.NewFormat(expfmt.TypeTextPlain)))
	res, err := s.Client.Do(req)
	if err != nil {
		return containerResource{}, store.ErrorUnreachable
	}
	defer func() { _ = res.Body.Close() }()
	switch {
	case res.StatusCode == http.StatusUnauthorized:
		return containerResource{}, store.ErrorUnauthenticated
	case res.StatusCode == http.StatusForbidden:
		return containerResource{}, store.ErrorPermissionDenied
	case res.StatusCode == http.StatusBadGateway || res.StatusCode == http.StatusServiceUnavailable || res.StatusCode == http.StatusGatewayTimeout:
		return containerResource{}, store.ErrorUnreachable
	case res.StatusCode < 200 || res.StatusCode > 299:
		return containerResource{}, store.ErrorOther
	}
	body, err := readCapped(res.Body, maxMetricsBody)
	if err != nil {
		if cctx.Err() != nil {
			return containerResource{}, store.ErrorUnreachable
		}
		return containerResource{}, store.ErrorOther // too large, or cut off mid-body
	}
	resource, err := parseContainerResource(body, containerID, s.Now())
	if err != nil {
		return containerResource{}, store.ErrorOther
	}
	return resource, ""
}
