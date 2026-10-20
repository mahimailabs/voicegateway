package stream

import (
	"context"
	"testing"
	"time"
)

func TestLiveIsolationAndCleanup(t *testing.T) {
	rdb := startRedis(t)
	live := NewLive(rdb.Options(), nil)
	t.Cleanup(func() { _ = live.Close() })
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	updates, closeWatch, err := live.Watch(ctx, "org", "one")
	if err != nil {
		t.Fatal(err)
	}
	defer closeWatch()
	live.Notify(ctx, "org", "two")
	live.Notify(ctx, "other", "one")
	select {
	case <-updates:
		t.Fatal("another scope reached subscriber")
	case <-time.After(50 * time.Millisecond):
	}
	live.Notify(ctx, "org", "one")
	select {
	case _, ok := <-updates:
		if !ok {
			t.Fatal("subscription closed")
		}
	case <-ctx.Done():
		t.Fatal("no invalidation")
	}
	closeWatch()
	select {
	case _, ok := <-updates:
		if ok {
			t.Fatal("unexpected pending hint")
		}
	case <-ctx.Done():
		t.Fatal("subscription leaked")
	}
}

func TestLiveRedisDisconnectClosesSubscription(t *testing.T) {
	rdb := startRedis(t)
	live := NewLive(rdb.Options(), nil)
	t.Cleanup(func() { _ = live.Close() })
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	updates, closeWatch, err := live.Watch(ctx, "org", "one")
	if err != nil {
		t.Fatal(err)
	}
	defer closeWatch()
	// Redis disconnects the actual pub/sub connection; the client must not hide it with reconnect.
	if err := rdb.Do(ctx, "CLIENT", "KILL", "TYPE", "pubsub").Err(); err != nil {
		t.Fatal(err)
	}
	select {
	case _, ok := <-updates:
		if ok {
			t.Fatal("disconnect reported as a change")
		}
	case <-ctx.Done():
		t.Fatal("disconnect was hidden")
	}
}

func TestLiveChannelBoundaries(t *testing.T) {
	if liveChannel("a:b", "c") == liveChannel("a", "b:c") {
		t.Fatal("scope collision")
	}
}

func TestLivePublishUnavailableIsBounded(t *testing.T) {
	rdb := startRedis(t)
	live := NewLive(rdb.Options(), nil)
	t.Cleanup(func() { _ = live.Close() })
	ctx := context.Background()
	// Establish the notification connection before pausing Redis command execution.
	live.Notify(ctx, "org", "p")
	if err := rdb.Do(ctx, "CLIENT", "PAUSE", 3000, "ALL").Err(); err != nil {
		t.Fatal(err)
	}
	started := time.Now()
	live.Notify(ctx, "org", "p")
	if elapsed := time.Since(started); elapsed > time.Second {
		t.Fatalf("notification blocked ingest for %s", elapsed)
	}
}
