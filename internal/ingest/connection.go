package ingest

import (
	"context"
	"log/slog"
	"sync"
	"time"

	lru "github.com/hashicorp/golang-lru/v2"
)

// connectionThrottle is how long an outcome already written for a project's source is not
// written again. A different outcome is written at once.
const connectionThrottle = 30 * time.Second

// trackedConnections bounds the outcomes remembered for throttling. A source evicted early costs
// one more write, never a lost outcome.
const trackedConnections = 10_000

// maxUsedKey bounds the refused token's key kept for the console; the column allows 64 bytes.
const maxUsedKey = 64

// ConnectionStore records an ingestion outcome: accepted when reason is empty. It reports whether
// the project exists, so an unknown project records nothing.
type ConnectionStore interface {
	RecordConnection(ctx context.Context, org, project, source, reason, usedKey string, at time.Time) (bool, error)
}

// Connections records how each project's ingestion last went, per source, as request state in
// Postgres (docs/specs/first-call-setup.md). It writes an outcome when it differs from the last one
// written for that project and source, or 30 s after it, so a busy agent adds no database round
// trip per upload. A failed write is logged and never fails the request.
type Connections struct {
	Store ConnectionStore
	Log   *slog.Logger
	Now   func() time.Time

	once    sync.Once
	written *lru.Cache[connectionKey, written]
}

type connectionKey struct{ org, project, source string }

type written struct {
	reason string
	at     time.Time
}

// Outcome is one request's recorder, bound to the project and source its URL names.
type Outcome struct {
	c   *Connections
	key connectionKey
}

// For returns the recorder for a request to the project's source. A nil Connections records
// nothing.
func (c *Connections) For(org, project, source string) Outcome {
	return Outcome{c: c, key: connectionKey{org, project, source}}
}

// Accepted records that the request was verified and appended.
func (o Outcome) Accepted(ctx context.Context) { o.record(ctx, "", "") }

// Rejected records why the request was refused. usedKey is kept only for a key mismatch.
func (o Outcome) Rejected(ctx context.Context, reason, usedKey string) {
	if len(usedKey) > maxUsedKey {
		usedKey = usedKey[:maxUsedKey]
	}
	o.record(ctx, reason, usedKey)
}

func (o Outcome) record(ctx context.Context, reason, usedKey string) {
	c := o.c
	if c == nil || c.Store == nil {
		return
	}
	c.once.Do(func() {
		// lru.New fails only for a size below 1.
		c.written, _ = lru.New[connectionKey, written](trackedConnections)
	})
	now := c.Now()
	if last, ok := c.written.Get(o.key); ok && last.reason == reason && now.Sub(last.at) < connectionThrottle {
		return
	}
	exists, err := c.Store.RecordConnection(context.WithoutCancel(ctx), o.key.org, o.key.project, o.key.source, reason, usedKey, now)
	if err != nil {
		c.Log.Warn("connection: outcome not recorded", "project", o.key.project, "source", o.key.source, "err", err)
		return
	}
	// An unknown project is not remembered, so made-up URLs cannot fill the cache.
	if exists {
		c.written.Add(o.key, written{reason: reason, at: now})
	}
}
