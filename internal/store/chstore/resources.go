package chstore

import (
	"context"
	"fmt"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// StoreResourceRead writes one read of a project's resource metrics endpoints: the samples first, then
// each endpoint's read, then the layer read, so a read recorded as good always has its samples
// behind it.
func (s *Store) StoreResourceRead(ctx context.Context, read store.LayerRead, endpoints []store.WorkerEndpointRead, samples []store.ResourceSample) error {
	if len(samples) > 0 {
		batch, err := s.conn.PrepareBatch(pollInsert(ctx), `INSERT INTO resource_samples (organisation_id, project_id, read_at, endpoint_id, observed_at, started_at, cpu_seconds, cpu_used, cpu_cores, memory_bytes, memory_limit_bytes)`)
		if err != nil {
			return fmt.Errorf("prepare resource samples: %w", err)
		}
		for _, w := range samples {
			if err := batch.Append(w.OrganisationID, w.ProjectID, w.ReadAt, w.EndpointID, w.ObservedAt, w.StartedAt, w.CPUSeconds, w.CPUUsed, w.CPUCores, w.MemoryBytes, w.MemoryLimitBytes); err != nil {
				_ = batch.Abort()
				return fmt.Errorf("resource sample %s: %w", w.EndpointID, err)
			}
		}
		if err := batch.Send(); err != nil {
			return fmt.Errorf("insert %d resource samples: %w", len(samples), err)
		}
	}
	if len(endpoints) > 0 {
		batch, err := s.conn.PrepareBatch(pollInsert(ctx), `INSERT INTO resource_endpoint_reads (organisation_id, project_id, endpoint_id, read_at, ok, error_class, every_ms)`)
		if err != nil {
			return fmt.Errorf("prepare resource endpoint reads: %w", err)
		}
		for _, e := range endpoints {
			every := uint32(e.Every.Milliseconds()) //nolint:gosec // intervals are seconds, far below 2^32 ms
			if err := batch.Append(read.OrganisationID, read.ProjectID, e.EndpointID, e.ReadAt, e.OK, e.ErrorClass, every); err != nil {
				_ = batch.Abort()
				return fmt.Errorf("resource endpoint read %s: %w", e.EndpointID, err)
			}
		}
		if err := batch.Send(); err != nil {
			return fmt.Errorf("insert %d resource endpoint reads: %w", len(endpoints), err)
		}
	}
	return s.storeLayerRead(ctx, read)
}

// ResourceSamples returns a project's resource samples since a time, by endpoint, oldest
// first, at most limit.
func (s *Store) ResourceSamples(ctx context.Context, organisationID, projectID string, since time.Time, limit int) ([]store.ResourceSample, error) {
	rows, err := s.conn.Query(ctx, `
		SELECT read_at, endpoint_id, observed_at, started_at, cpu_seconds, cpu_used, cpu_cores, memory_bytes, memory_limit_bytes FROM resource_samples
		WHERE organisation_id = ? AND project_id = ? AND read_at >= ?
		ORDER BY endpoint_id, read_at
		LIMIT ?`, organisationID, projectID, since, limit)
	if err != nil {
		return nil, fmt.Errorf("resource samples: %w", err)
	}
	defer func() { _ = rows.Close() }()
	var out []store.ResourceSample
	for rows.Next() {
		w := store.ResourceSample{OrganisationID: organisationID, ProjectID: projectID}
		if err := rows.Scan(&w.ReadAt, &w.EndpointID, &w.ObservedAt, &w.StartedAt, &w.CPUSeconds, &w.CPUUsed, &w.CPUCores, &w.MemoryBytes, &w.MemoryLimitBytes); err != nil {
			return nil, fmt.Errorf("resource samples: %w", err)
		}
		w.ReadAt = w.ReadAt.UTC()
		out = append(out, w)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("resource samples: %w", err)
	}
	return out, nil
}

// ResourceEndpointStatus sums up every read of each of a project's endpoints, however old, by
// endpoint id. An endpoint never read is absent.
func (s *Store) ResourceEndpointStatus(ctx context.Context, organisationID, projectID string) (map[string]store.LayerStatus, error) {
	rows, err := s.conn.Query(ctx, `
		SELECT endpoint_id, max(read_at), countIf(ok), maxIf(read_at, ok), argMax(error_class, read_at)
		FROM resource_endpoint_reads WHERE organisation_id = ? AND project_id = ?
		GROUP BY endpoint_id`, organisationID, projectID)
	if err != nil {
		return nil, fmt.Errorf("resource endpoint status: %w", err)
	}
	defer func() { _ = rows.Close() }()
	out := map[string]store.LayerStatus{}
	for rows.Next() {
		var (
			id             string
			last, lastGood time.Time
			good           uint64
			class          string
		)
		if err := rows.Scan(&id, &last, &good, &lastGood, &class); err != nil {
			return nil, fmt.Errorf("resource endpoint status: %w", err)
		}
		last = last.UTC()
		st := store.LayerStatus{LastReadAt: &last, ErrorClass: class}
		if good > 0 {
			t := lastGood.UTC()
			st.LastGoodReadAt = &t
		}
		out[id] = st
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("resource endpoint status: %w", err)
	}
	return out, nil
}
