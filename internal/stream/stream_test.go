package stream

import (
	"bytes"
	"context"
	"os"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/testcontainers/testcontainers-go"
	tcredis "github.com/testcontainers/testcontainers-go/modules/redis"

	"github.com/mahimailabs/voicegateway/internal/event"
)

// The same image, tag and digest as deploy/compose.yaml.
const redisImage = "redis:8.10.2-alpine@sha256:3811787313eba226a2ef38658c6ccb91cd5e110edc89c37767de373120a0e5a0"

// startRedis skips with -short or without Docker on a laptop; in CI a missing Docker fails the test.
func startRedis(t *testing.T) *redis.Client {
	t.Helper()
	if os.Getenv("CI") == "" {
		if testing.Short() {
			t.Skip("needs Docker; runs in CI")
		}
		testcontainers.SkipIfProviderIsNotHealthy(t)
	}
	ctx := context.Background()
	rd, err := tcredis.Run(ctx, redisImage)
	testcontainers.CleanupContainer(t, rd)
	if err != nil {
		t.Fatalf("start redis: %v", err)
	}
	url, err := rd.ConnectionString(ctx)
	if err != nil {
		t.Fatal(err)
	}
	opts, err := redis.ParseURL(url)
	if err != nil {
		t.Fatal(err)
	}
	rdb := redis.NewClient(opts)
	t.Cleanup(func() { _ = rdb.Close() })
	return rdb
}

// An envelope appended by web comes out of the group unchanged, and once acked it is no longer
// pending, so a restarted worker does not store it twice.
func TestAppendReadAck(t *testing.T) {
	rdb := startRedis(t)
	ctx := context.Background()

	// Appended before the group exists, as when web starts before the worker.
	in := event.Envelope{
		ID: "EV_1", Kind: event.KindLiveKitWebhook, OrganisationID: "default", ProjectID: "production",
		ReceivedAt: time.Date(2026, 10, 16, 12, 0, 0, 123456789, time.UTC), Payload: []byte(`{"event":"room_started"}`),
	}
	if _, err := NewProducer(rdb, Ingest).Append(ctx, in); err != nil {
		t.Fatal(err)
	}

	c := NewConsumer(rdb, Ingest, "worker", "w1")
	for range 2 { // a second Ensure, as on restart, is not an error
		if err := c.Ensure(ctx); err != nil {
			t.Fatal(err)
		}
	}
	msgs, err := c.Read(ctx, 10, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if len(msgs) != 1 || msgs[0].Err != nil {
		t.Fatalf("read %+v, want one decoded entry", msgs)
	}
	got := msgs[0].Envelope
	if got.ID != in.ID || got.Kind != in.Kind || got.OrganisationID != in.OrganisationID || got.ProjectID != in.ProjectID ||
		!got.ReceivedAt.Equal(in.ReceivedAt) || !bytes.Equal(got.Payload, in.Payload) {
		t.Fatalf("envelope changed in transit:\n got %+v\nwant %+v", got, in)
	}

	if n := rdb.XPending(ctx, Ingest, "worker").Val().Count; n != 1 {
		t.Fatalf("pending before ack = %d, want 1", n)
	}
	if err := c.Ack(ctx, msgs[0].ID); err != nil {
		t.Fatal(err)
	}
	if n := rdb.XPending(ctx, Ingest, "worker").Val().Count; n != 0 {
		t.Fatalf("pending after ack = %d, want 0", n)
	}
}

// An entry in a layout the reader does not know is returned with an error and its id, not dropped.
func TestReadUnknownLayout(t *testing.T) {
	rdb := startRedis(t)
	ctx := context.Background()
	c := NewConsumer(rdb, Ingest, "worker", "w1")
	if err := c.Ensure(ctx); err != nil {
		t.Fatal(err)
	}
	id := rdb.XAdd(ctx, &redis.XAddArgs{Stream: Ingest, Values: []any{"v", "9", "payload", "x"}}).Val()
	msgs, err := c.Read(ctx, 10, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if len(msgs) != 1 || msgs[0].ID != id || msgs[0].Err == nil {
		t.Fatalf("read %+v, want entry %s with a decode error", msgs, id)
	}
}

// An envelope without a project is refused before it reaches the stream.
func TestAppendRefusesUnattributed(t *testing.T) {
	rdb := startRedis(t)
	_, err := NewProducer(rdb, Ingest).Append(context.Background(), event.Envelope{Kind: event.KindLiveKitWebhook, ReceivedAt: time.Now()})
	if err == nil {
		t.Fatal("append without a project succeeded")
	}
	if n := rdb.XLen(context.Background(), Ingest).Val(); n != 0 {
		t.Fatalf("stream length = %d, want 0", n)
	}
}
