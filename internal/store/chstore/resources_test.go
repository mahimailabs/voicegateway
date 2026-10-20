package chstore

import (
	"context"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestResourceReadPersistenceAndFailure(t *testing.T) {
	s := openMigrated(t)
	ctx := context.Background()
	now := time.Now().UTC().Truncate(time.Millisecond)
	memory := 1048576.0
	read := store.LayerRead{OrganisationID: "org", ProjectID: "p", Layer: store.LayerResources, ReadAt: now, OK: true, Every: 15 * time.Second}
	endpoint := store.WorkerEndpointRead{EndpointID: "resource", ReadAt: now, OK: true, Every: 15 * time.Second}
	sample := store.ResourceSample{OrganisationID: "org", ProjectID: "p", EndpointID: "resource", ReadAt: now, ObservedAt: now.Add(-time.Second), MemoryBytes: &memory}
	if err := s.StoreResourceRead(ctx, read, []store.WorkerEndpointRead{endpoint}, []store.ResourceSample{sample}); err != nil {
		t.Fatal(err)
	}
	got, err := s.ResourceSamples(ctx, "org", "p", now.Add(-time.Minute), 10)
	if err != nil || len(got) != 1 {
		t.Fatal("durable sample absent", err)
	}
	if got[0].CPUUsed != nil || got[0].CPUCores != nil || got[0].MemoryLimitBytes != nil || got[0].MemoryBytes == nil || *got[0].MemoryBytes != memory || !got[0].ObservedAt.Equal(sample.ObservedAt) {
		t.Fatalf("lost nulls or observation: %+v", got[0])
	}
	if other, err := s.ResourceSamples(ctx, "other", "p", now.Add(-time.Minute), 10); err != nil || len(other) != 0 {
		t.Fatal("cross-org sample", err)
	}
	read.ReadAt = now.Add(time.Second)
	read.OK = false
	read.ErrorClass = store.ErrorUnreachable
	endpoint.ReadAt = read.ReadAt
	endpoint.OK = false
	endpoint.ErrorClass = read.ErrorClass
	if err := s.StoreResourceRead(ctx, read, []store.WorkerEndpointRead{endpoint}, nil); err != nil {
		t.Fatal(err)
	}
	statuses, err := s.ResourceEndpointStatus(ctx, "org", "p")
	if err != nil {
		t.Fatal(err)
	}
	status := statuses["resource"]
	if status.ErrorClass != store.ErrorUnreachable || status.LastGoodReadAt == nil || !status.LastGoodReadAt.Equal(now) {
		t.Fatalf("failure erased prior evidence: %+v", status)
	}
	got, err = s.ResourceSamples(ctx, "org", "p", now.Add(-time.Minute), 10)
	if err != nil || len(got) != 1 {
		t.Fatal("failed read invented or erased sample", err)
	}
}
