package ingest

import (
	"context"
	"io"
	"log/slog"
	"slices"
	"strings"
	"sync"
	"testing"
	"testing/synctest"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

type countingConnections struct {
	mu     sync.Mutex
	writes []string
	known  bool
}

func (c *countingConnections) RecordConnection(_ context.Context, _, _, _, reason, _ string, _ time.Time) (bool, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.writes = append(c.writes, reason)
	return c.known, nil
}

// TestConnectionThrottle: 100 accepted uploads in 30 s produce one write; a different outcome is
// written at once, and the same outcome again after 30 s (docs/specs/first-call-setup.md check 6).
func TestConnectionThrottle(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		db := &countingConnections{known: true}
		c := &Connections{Store: db, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: time.Now}
		o := c.For("acme", "voice", store.SourceAgent)
		ctx := context.Background()
		for range 100 {
			o.Accepted(ctx)
			time.Sleep(290 * time.Millisecond)
		}
		if len(db.writes) != 1 {
			t.Fatalf("100 uploads in 29 s wrote %d times", len(db.writes))
		}
		o.Rejected(ctx, store.ReasonSignatureInvalid, "")
		o.Rejected(ctx, store.ReasonSignatureInvalid, "")
		o.Accepted(ctx)
		if want := []string{"", store.ReasonSignatureInvalid, ""}; !slices.Equal(db.writes, want) {
			t.Fatalf("writes %q, want %q", db.writes, want)
		}
		time.Sleep(connectionThrottle)
		o.Accepted(ctx)
		if len(db.writes) != 4 {
			t.Fatalf("an outcome 30 s old was not written again: %q", db.writes)
		}
		// Another source of the same project is throttled on its own.
		c.For("acme", "voice", store.SourceWebhook).Accepted(ctx)
		if len(db.writes) != 5 {
			t.Fatalf("webhook outcome not written: %q", db.writes)
		}
	})
}

// TestConnectionUnknownProject: requests to a project that does not exist cost one store call per
// 30 s and never enter the cache of real projects.
func TestConnectionUnknownProject(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		db := &countingConnections{known: false}
		c := &Connections{Store: db, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: time.Now}
		for range 100 {
			c.For("nobody", "voice", store.SourceWebhook).Rejected(context.Background(), store.ReasonTokenMissing, "")
			time.Sleep(290 * time.Millisecond)
		}
		if len(db.writes) != 1 || c.written.Len() != 0 {
			t.Fatalf("100 requests in 29 s: %d store calls, %d real projects cached", len(db.writes), c.written.Len())
		}
		time.Sleep(connectionThrottle)
		c.For("nobody", "voice", store.SourceWebhook).Rejected(context.Background(), store.ReasonTokenMissing, "")
		if len(db.writes) != 2 {
			t.Fatalf("not asked again after 30 s: %d store calls", len(db.writes))
		}
	})
	var none *Connections
	none.For("a", "b", store.SourceAgent).Accepted(context.Background())
}

// TestConnectionUsedKeyOnlyWellFormed: the key a refused token named is kept only in a LiveKit API
// key's shape; text the sender made up, too long, or not UTF-8 is stored as unknown.
func TestConnectionUsedKeyOnlyWellFormed(t *testing.T) {
	var got string
	db := connectionFunc(func(usedKey string) { got = usedKey })
	c := &Connections{Store: db, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: time.Now}
	for _, tc := range []struct{ name, key, want string }{
		{"livekit key", "APIkpuATgWarLxL", "APIkpuATgWarLxL"},
		{"64 characters", strings.Repeat("k", 64), strings.Repeat("k", 64)},
		{"65 characters", strings.Repeat("k", 65), ""},
		{"spaces and markup", "<b>use my key</b>", ""},
		{"multibyte", strings.Repeat("k", 63) + "€", ""},
		{"invalid UTF-8", "API\xff", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c.For("a", tc.name, store.SourceAgent).Rejected(context.Background(), store.ReasonKeyMismatch, tc.key)
			if got != tc.want {
				t.Fatalf("kept %q, want %q", got, tc.want)
			}
		})
	}
}

type connectionFunc func(usedKey string)

func (f connectionFunc) RecordConnection(_ context.Context, _, _, _, _, usedKey string, _ time.Time) (bool, error) {
	f(usedKey)
	return true, nil
}
