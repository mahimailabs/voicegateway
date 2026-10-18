package auth

import (
	"context"
	"crypto/sha256"
	"fmt"
	"sync"
	"time"

	"github.com/alexedwards/scs/v2"
)

// QuietStore wraps a scs store to skip the write scs makes on every request. With an idle timeout,
// scs marks every loaded session modified, so each API call rewrites the session row only to move
// its expiry (scs v2.9.0 data.go). QuietStore skips that commit while the session's bytes are the
// ones this request read and the expiry has moved less than Slack past the last one this process
// wrote. scs has no option for this. It keeps an entry only for a session the store found or this
// process wrote, keyed by the token's SHA-256, so forged cookies cannot grow it.
//
// Skipping is always safe: the row already holds these bytes, and an expiry at most Slack older
// than the newest is harmless against a 12-hour idle timeout. After a restart, or for a session
// another replica wrote, nothing is known and the commit goes through. It records hashes of
// session bytes and their expiries, never a token's data.
type QuietStore struct {
	inner scs.CtxStore
	slack time.Duration
	now   func() time.Time

	mu   sync.Mutex
	seen map[[sha256.Size]byte]seenSession
	ops  int
}

// seenSession is what this process last knew of one session's row.
type seenSession struct {
	read      [sha256.Size]byte // the fingerprint of what the last Find returned
	readOK    bool
	committed time.Time // the expiry this process last wrote, zero if none
	touched   time.Time
}

// sweepEvery is how many operations pass between sweeps of entries idle longer than an hour.
const sweepEvery = 1024

// NewQuietStore wraps inner, skipping commits whose expiry moved less than slack.
func NewQuietStore(inner scs.CtxStore, slack time.Duration) *QuietStore {
	return &QuietStore{inner: inner, slack: slack, now: time.Now, seen: map[[sha256.Size]byte]seenSession{}}
}

// FindCtx reads a session and remembers which bytes the row held. A token the store does not hold
// is not remembered: scs passes any cookie value through, so an unknown one must not cost memory.
func (q *QuietStore) FindCtx(ctx context.Context, token string) ([]byte, bool, error) {
	b, found, err := q.inner.FindCtx(ctx, token)
	if err != nil || !found {
		q.forget(token)
		return b, found, err
	}
	key := sha256.Sum256([]byte(token))
	q.mu.Lock()
	defer q.mu.Unlock()
	e := q.seen[key]
	e.read, e.readOK, e.touched = fingerprint(b), true, q.now()
	q.seen[key] = e
	q.sweep()
	return b, true, nil
}

// fingerprint identifies what a session holds, not how it was encoded: scs's gob codec writes a
// map's keys in random order, so one session can encode to different bytes, and comparing bytes
// would miss a match and write. It decodes with scs's default codec, the one NewSessions uses, and
// hashes the deadline and the values, which fmt prints in sorted key order. Bytes it cannot decode
// are hashed as they are.
func fingerprint(b []byte) [sha256.Size]byte {
	deadline, values, err := (scs.GobCodec{}).Decode(b)
	if err != nil {
		return sha256.Sum256(b)
	}
	return sha256.Sum256(fmt.Appendf(nil, "%d|%v", deadline.UnixNano(), values))
}

// forget drops what is known of a token, if anything.
func (q *QuietStore) forget(token string) {
	key := sha256.Sum256([]byte(token))
	q.mu.Lock()
	delete(q.seen, key)
	q.mu.Unlock()
}

// CommitCtx writes a session unless the row already holds these bytes and the expiry moved less
// than the slack past the last one written here.
func (q *QuietStore) CommitCtx(ctx context.Context, token string, b []byte, expiry time.Time) error {
	h, key := fingerprint(b), sha256.Sum256([]byte(token))
	q.mu.Lock()
	e, ok := q.seen[key]
	skip := ok && e.readOK && e.read == h && !e.committed.IsZero() && expiry.Sub(e.committed) < q.slack
	q.mu.Unlock()
	if skip {
		return nil
	}
	if err := q.inner.CommitCtx(ctx, token, b, expiry); err != nil {
		return err
	}
	q.mu.Lock()
	defer q.mu.Unlock()
	q.seen[key] = seenSession{read: h, readOK: true, committed: expiry, touched: q.now()}
	q.sweep()
	return nil
}

// DeleteCtx deletes a session and forgets it.
func (q *QuietStore) DeleteCtx(ctx context.Context, token string) error {
	q.forget(token)
	return q.inner.DeleteCtx(ctx, token)
}

// Find, Commit and Delete are the context-free forms scs.Store asks for.
func (q *QuietStore) Find(token string) ([]byte, bool, error) {
	return q.FindCtx(context.Background(), token)
}

func (q *QuietStore) Commit(token string, b []byte, expiry time.Time) error {
	return q.CommitCtx(context.Background(), token, b, expiry)
}

func (q *QuietStore) Delete(token string) error {
	return q.DeleteCtx(context.Background(), token)
}

// sweep drops entries idle for an hour, every sweepEvery operations; a dropped session just
// commits on its next request. The caller holds q.mu.
func (q *QuietStore) sweep() {
	q.ops++
	if q.ops%sweepEvery != 0 {
		return
	}
	cutoff := q.now().Add(-time.Hour)
	for token, e := range q.seen {
		if e.touched.Before(cutoff) {
			delete(q.seen, token)
		}
	}
}
