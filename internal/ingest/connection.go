package ingest

import (
	"context"
	"log/slog"
	"regexp"
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

// unknownProjects bounds the made-up projects remembered, so a stream of invented /ingest URLs
// costs at most one database round trip per URL every 30 s and never evicts real projects.
const unknownProjects = 1_000

// recordTimeout bounds one outcome write; the request it describes has already been answered or
// is about to be.
const recordTimeout = 2 * time.Second

// liveKitKey is the shape of a LiveKit API key. A refused token's key is kept for the console only
// in that shape: the sender chooses it, so anything else is stored as unknown, never shown.
var liveKitKey = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

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
	// unknown remembers when a project was found not to exist.
	unknown *lru.Cache[connectionKey, time.Time]
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

// Rejected records why the request was refused. usedKey is kept only for a key mismatch, and only
// when it is shaped like a LiveKit API key.
func (o Outcome) Rejected(ctx context.Context, reason, usedKey string) {
	if !liveKitKey.MatchString(usedKey) {
		usedKey = ""
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
		c.unknown, _ = lru.New[connectionKey, time.Time](unknownProjects)
	})
	now := c.Now()
	if last, ok := c.written.Get(o.key); ok && last.reason == reason && now.Sub(last.at) < connectionThrottle {
		return
	}
	if at, ok := c.unknown.Get(o.key); ok && now.Sub(at) < connectionThrottle {
		return
	}
	// The write outlives a client that hangs up, but not a slow database.
	wctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), recordTimeout)
	defer cancel()
	exists, err := c.Store.RecordConnection(wctx, o.key.org, o.key.project, o.key.source, reason, usedKey, now)
	if err != nil {
		c.Log.Warn("connection: outcome not recorded", "project", o.key.project, "source", o.key.source, "err", err)
		return
	}
	// A project that does not exist goes in its own small cache, so made-up URLs never evict a
	// real project's throttle.
	if !exists {
		c.unknown.Add(o.key, now)
		return
	}
	c.written.Add(o.key, written{reason: reason, at: now})
}
