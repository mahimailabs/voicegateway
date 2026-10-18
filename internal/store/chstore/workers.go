package chstore

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// StoreWorkerRead writes one read of a project's worker metrics endpoints: the samples first, then
// each endpoint's read, then the layer read, so a read recorded as good always has its samples
// behind it.
func (s *Store) StoreWorkerRead(ctx context.Context, read store.LayerRead, endpoints []store.WorkerEndpointRead, samples []store.WorkerSample) error {
	if len(samples) > 0 {
		batch, err := s.conn.PrepareBatch(pollInsert(ctx), `INSERT INTO worker_samples (organisation_id, project_id, read_at, endpoint_id, nodename, load, active_jobs)`)
		if err != nil {
			return fmt.Errorf("prepare worker samples: %w", err)
		}
		for _, w := range samples {
			if err := batch.Append(w.OrganisationID, w.ProjectID, w.ReadAt, w.EndpointID, w.Nodename, w.Load, w.ActiveJobs); err != nil {
				_ = batch.Abort()
				return fmt.Errorf("worker sample %s: %w", w.EndpointID, err)
			}
		}
		if err := batch.Send(); err != nil {
			return fmt.Errorf("insert %d worker samples: %w", len(samples), err)
		}
	}
	if len(endpoints) > 0 {
		batch, err := s.conn.PrepareBatch(pollInsert(ctx), `INSERT INTO worker_endpoint_reads (organisation_id, project_id, endpoint_id, read_at, ok, error_class, every_ms)`)
		if err != nil {
			return fmt.Errorf("prepare worker endpoint reads: %w", err)
		}
		for _, e := range endpoints {
			every := uint32(e.Every.Milliseconds()) //nolint:gosec // intervals are seconds, far below 2^32 ms
			if err := batch.Append(read.OrganisationID, read.ProjectID, e.EndpointID, e.ReadAt, e.OK, e.ErrorClass, every); err != nil {
				_ = batch.Abort()
				return fmt.Errorf("worker endpoint read %s: %w", e.EndpointID, err)
			}
		}
		if err := batch.Send(); err != nil {
			return fmt.Errorf("insert %d worker endpoint reads: %w", len(endpoints), err)
		}
	}
	return s.storeLayerRead(ctx, read)
}

// WorkerSamples returns a project's worker samples since a time, by endpoint and nodename, oldest
// first, at most limit.
func (s *Store) WorkerSamples(ctx context.Context, organisationID, projectID string, since time.Time, limit int) ([]store.WorkerSample, error) {
	rows, err := s.conn.Query(ctx, `
		SELECT read_at, endpoint_id, nodename, load, active_jobs FROM worker_samples
		WHERE organisation_id = ? AND project_id = ? AND read_at >= ?
		ORDER BY endpoint_id, nodename, read_at
		LIMIT ?`, organisationID, projectID, since, limit)
	if err != nil {
		return nil, fmt.Errorf("worker samples: %w", err)
	}
	defer func() { _ = rows.Close() }()
	var out []store.WorkerSample
	for rows.Next() {
		w := store.WorkerSample{OrganisationID: organisationID, ProjectID: projectID}
		if err := rows.Scan(&w.ReadAt, &w.EndpointID, &w.Nodename, &w.Load, &w.ActiveJobs); err != nil {
			return nil, fmt.Errorf("worker samples: %w", err)
		}
		w.ReadAt = w.ReadAt.UTC()
		out = append(out, w)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("worker samples: %w", err)
	}
	return out, nil
}

// WorkerEndpointStatus sums up every read of each of a project's endpoints, however old, by
// endpoint id. An endpoint never read is absent.
func (s *Store) WorkerEndpointStatus(ctx context.Context, organisationID, projectID string) (map[string]store.LayerStatus, error) {
	rows, err := s.conn.Query(ctx, `
		SELECT endpoint_id, max(read_at), countIf(ok), maxIf(read_at, ok), argMax(error_class, read_at)
		FROM worker_endpoint_reads WHERE organisation_id = ? AND project_id = ?
		GROUP BY endpoint_id`, organisationID, projectID)
	if err != nil {
		return nil, fmt.Errorf("worker endpoint status: %w", err)
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
			return nil, fmt.Errorf("worker endpoint status: %w", err)
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
		return nil, fmt.Errorf("worker endpoint status: %w", err)
	}
	return out, nil
}

// StoreCloudAgentsRead writes one Cloud agents read: what it saw first, when it succeeded, then the
// read itself.
func (s *Store) StoreCloudAgentsRead(ctx context.Context, read store.LayerRead, agents *store.CloudAgents) error {
	if read.OK && agents != nil {
		body, err := json.Marshal(agents)
		if err != nil {
			return fmt.Errorf("encode cloud agents: %w", err)
		}
		batch, err := s.conn.PrepareBatch(pollInsert(ctx), `INSERT INTO cloud_agent_reads (organisation_id, project_id, read_at, agents)`)
		if err != nil {
			return fmt.Errorf("prepare cloud agents read: %w", err)
		}
		if err := batch.Append(read.OrganisationID, read.ProjectID, read.ReadAt, string(body)); err != nil {
			_ = batch.Abort()
			return fmt.Errorf("cloud agents read: %w", err)
		}
		if err := batch.Send(); err != nil {
			return fmt.Errorf("insert cloud agents read: %w", err)
		}
	}
	return s.storeLayerRead(ctx, read)
}

// LatestCloudAgents returns the latest good Cloud agents read of a project, or store.ErrNotFound
// when there is none.
func (s *Store) LatestCloudAgents(ctx context.Context, organisationID, projectID string) (store.CloudAgentsRead, error) {
	var out store.CloudAgentsRead
	var body string
	err := s.conn.QueryRow(ctx, `
		SELECT read_at, agents FROM cloud_agent_reads
		WHERE organisation_id = ? AND project_id = ?
		ORDER BY read_at DESC LIMIT 1`, organisationID, projectID).Scan(&out.ReadAt, &body)
	if errors.Is(err, sql.ErrNoRows) {
		return out, store.ErrNotFound
	}
	if err != nil {
		return out, fmt.Errorf("latest cloud agents read: %w", err)
	}
	if err := json.Unmarshal([]byte(body), &out.CloudAgents); err != nil {
		return out, fmt.Errorf("decode cloud agents read: %w", err)
	}
	out.ReadAt = out.ReadAt.UTC()
	return out, nil
}
