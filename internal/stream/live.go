package stream

import (
	"context"
	"encoding/base64"
	"fmt"
	"log/slog"
	"time"

	"github.com/redis/go-redis/v9"
)

// Live carries coalescible project invalidations, never telemetry. Durable snapshots remain the
// authority. Pub/sub is deliberately not a replay log (decision 0007).
type Live struct {
	client *redis.Client
	Log    *slog.Logger
}

// NewLive uses its own bounded connection pool so notification failures cannot occupy ingest
// connections. ContextTimeoutEnabled makes Redis reads honor the notification deadline too.
func NewLive(options *redis.Options, log *slog.Logger) *Live {
	opts := *options
	opts.MaxRetries = -1
	opts.ContextTimeoutEnabled = true
	opts.DialTimeout = 250 * time.Millisecond
	opts.PoolTimeout = 250 * time.Millisecond
	return &Live{client: redis.NewClient(&opts), Log: log}
}

func (l *Live) Close() error { return l.client.Close() }

func liveChannel(org, project string) string {
	encode := base64.RawURLEncoding.EncodeToString
	return "voicegateway:live:" + encode([]byte(org)) + ":" + encode([]byte(project))
}

// Notify is best effort and bounded: a failed notification must never fail durable ingestion.
func (l *Live) Notify(ctx context.Context, org, project string) {
	if ctx.Err() != nil {
		return
	}
	ctx, cancel := context.WithTimeout(ctx, 250*time.Millisecond)
	defer cancel()
	if err := l.client.Publish(ctx, liveChannel(org, project), "refresh").Err(); err != nil && l.Log != nil {
		l.Log.Warn("live notification unavailable", "organisation", org, "project", project)
	}
}

// Watch confirms Redis subscribed before returning. An interrupted Redis connection closes the
// channel: callers must reconnect and refresh snapshots, not silently miss changes. Slow clients
// coalesce refreshes into a single pending hint rather than block the publisher or grow a queue.
func (l *Live) Watch(ctx context.Context, org, project string) (<-chan struct{}, func(), error) {
	ctx, cancel := context.WithCancel(ctx)
	sub := l.client.Subscribe(ctx, liveChannel(org, project))
	stop := context.AfterFunc(ctx, func() { _ = sub.Close() })
	closeWatch := func() { cancel(); stop(); _ = sub.Close() }
	handshake, done := context.WithTimeout(ctx, 3*time.Second)
	stopHandshake := context.AfterFunc(handshake, func() { _ = sub.Close() })
	_, err := sub.Receive(handshake)
	stopHandshake()
	done()
	if err != nil {
		closeWatch()
		return nil, nil, fmt.Errorf("subscribe live updates: %w", err)
	}
	updates := make(chan struct{}, 1)
	go func() {
		defer close(updates)
		defer closeWatch()
		for {
			message, err := sub.ReceiveMessage(ctx)
			if err != nil {
				return
			}
			if message.Payload != "refresh" {
				continue
			}
			select {
			case updates <- struct{}{}:
			default:
			}
		}
	}()
	return updates, closeWatch, nil
}
