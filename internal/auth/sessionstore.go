package auth

import (
	"context"
	"crypto/sha256"
	"sync"
	"time"

	"github.com/alexedwards/scs/v2"
)

// QuietStore wraps a scs store to skip the write scs makes on every request. With an idle timeout,
// scs marks every loaded session modified, so each API call rewrites the session row only to move
// its expiry (scs v2.9.0 data.go). QuietStore skips that commit while the session's bytes are the
// ones this request read and the expiry has moved less than Slack past the last one this process
// wrote. scs has no option for this.
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
	seen map[string]seenSession
	ops  int
}

// seenSession is what this process last knew of one session's row.
type seenSession struct {
	read      [sha256.Size]byte // the bytes the last Find returned
	readOK    bool
	committed time.Time // the expiry this process last wrote, zero if none
	touched   time.Time
}

// sweepEvery is how many operations pass between sweeps of entries idle longer than an hour.
const sweepEvery = 1024

// NewQuietStore wraps inner, skipping commits whose expiry moved less than slack.
func NewQuietStore(inner scs.CtxStore, slack time.Duration) *QuietStore {
	return &QuietStore{inner: inner, slack: slack, now: time.Now, seen: map[string]seenSession{}}
}

// FindCtx reads a session and remembers which bytes the row held.
func (q *QuietStore) FindCtx(ctx context.Context, token string) ([]byte, bool, error) {
	b, found, err := q.inner.FindCtx(ctx, token)
	if err != nil {
		return b, found, err
	}
	q.mu.Lock()
	defer q.mu.Unlock()
	e := q.seen[token]
	e.readOK = found
	if found {
		e.read = sha256.Sum256(b)
	}
	e.touched = q.now()
	q.seen[token] = e
	q.sweep()
	return b, found, nil
}

// CommitCtx writes a session unless the row already holds these bytes and the expiry moved less
// than the slack past the last one written here.
func (q *QuietStore) CommitCtx(ctx context.Context, token string, b []byte, expiry time.Time) error {
	h := sha256.Sum256(b)
	q.mu.Lock()
	e, ok := q.seen[token]
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
	q.seen[token] = seenSession{read: h, readOK: true, committed: expiry, touched: q.now()}
	q.sweep()
	return nil
}

// DeleteCtx deletes a session and forgets it.
func (q *QuietStore) DeleteCtx(ctx context.Context, token string) error {
	q.mu.Lock()
	delete(q.seen, token)
	q.mu.Unlock()
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
