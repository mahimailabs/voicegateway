package auth

import (
	"context"
	"testing"
	"time"
)

// memSessions is an in-memory scs.CtxStore that counts its commits.
type memSessions struct {
	rows    map[string][]byte
	expiry  map[string]time.Time
	commits int
}

func newMemSessions() *memSessions {
	return &memSessions{rows: map[string][]byte{}, expiry: map[string]time.Time{}}
}

func (m *memSessions) FindCtx(_ context.Context, token string) ([]byte, bool, error) {
	b, ok := m.rows[token]
	return b, ok, nil
}

func (m *memSessions) CommitCtx(_ context.Context, token string, b []byte, expiry time.Time) error {
	m.commits++
	m.rows[token], m.expiry[token] = append([]byte(nil), b...), expiry
	return nil
}

// replicaWrite stands for another replica writing the row: it is not this store's commit.
func (m *memSessions) replicaWrite(token string, b []byte, expiry time.Time) {
	m.rows[token], m.expiry[token] = append([]byte(nil), b...), expiry
}

func (m *memSessions) DeleteCtx(_ context.Context, token string) error {
	delete(m.rows, token)
	return nil
}

func (m *memSessions) Find(token string) ([]byte, bool, error) {
	return m.FindCtx(context.Background(), token)
}

func (m *memSessions) Commit(token string, b []byte, expiry time.Time) error {
	return m.CommitCtx(context.Background(), token, b, expiry)
}
func (m *memSessions) Delete(token string) error { return m.DeleteCtx(context.Background(), token) }

// QuietStore writes a session when it changes, when its expiry has moved the slack, or when it knows
// nothing of the row; an unchanged session read and committed again within the slack is skipped.
func TestQuietStoreSkipsUnchangedCommits(t *testing.T) {
	ctx := context.Background()
	t0 := time.Date(2026, 10, 18, 12, 0, 0, 0, time.UTC)
	for _, tt := range []struct {
		name   string
		steps  func(q *QuietStore)
		writes int
	}{
		{"a new session is written", func(q *QuietStore) {
			_ = q.CommitCtx(ctx, "tok", []byte("a"), t0)
		}, 1},
		{"an unchanged request a minute later is skipped", func(q *QuietStore) {
			_ = q.CommitCtx(ctx, "tok", []byte("a"), t0)
			_, _, _ = q.FindCtx(ctx, "tok")
			_ = q.CommitCtx(ctx, "tok", []byte("a"), t0.Add(time.Minute))
		}, 1},
		{"a hundred unchanged requests within the slack are one write", func(q *QuietStore) {
			_ = q.CommitCtx(ctx, "tok", []byte("a"), t0)
			for i := range 100 {
				_, _, _ = q.FindCtx(ctx, "tok")
				_ = q.CommitCtx(ctx, "tok", []byte("a"), t0.Add(time.Duration(i)*time.Second))
			}
		}, 1},
		{"changed data is written at once", func(q *QuietStore) {
			_ = q.CommitCtx(ctx, "tok", []byte("a"), t0)
			_, _, _ = q.FindCtx(ctx, "tok")
			_ = q.CommitCtx(ctx, "tok", []byte("b"), t0.Add(time.Second))
		}, 2},
		{"the expiry moving the slack is written", func(q *QuietStore) {
			_ = q.CommitCtx(ctx, "tok", []byte("a"), t0)
			_, _, _ = q.FindCtx(ctx, "tok")
			_ = q.CommitCtx(ctx, "tok", []byte("a"), t0.Add(5*time.Minute))
		}, 2},
		{"a session this process never wrote is written once", func(q *QuietStore) {
			q.inner.(*memSessions).replicaWrite("tok", []byte("a"), t0) // another replica, or before a restart
			_, _, _ = q.FindCtx(ctx, "tok")
			_ = q.CommitCtx(ctx, "tok", []byte("a"), t0.Add(time.Second))
			_, _, _ = q.FindCtx(ctx, "tok")
			_ = q.CommitCtx(ctx, "tok", []byte("a"), t0.Add(2*time.Second))
		}, 1},
		{"bytes another replica changed since are written back when they differ", func(q *QuietStore) {
			_ = q.CommitCtx(ctx, "tok", []byte("a"), t0)
			q.inner.(*memSessions).replicaWrite("tok", []byte("b"), t0.Add(time.Second))
			_, _, _ = q.FindCtx(ctx, "tok")
			_ = q.CommitCtx(ctx, "tok", []byte("a"), t0.Add(2*time.Second))
		}, 2},
		{"a deleted session is written again", func(q *QuietStore) {
			_ = q.CommitCtx(ctx, "tok", []byte("a"), t0)
			_ = q.DeleteCtx(ctx, "tok")
			_ = q.CommitCtx(ctx, "tok", []byte("a"), t0.Add(time.Second))
		}, 2},
	} {
		t.Run(tt.name, func(t *testing.T) {
			inner := newMemSessions()
			q := NewQuietStore(inner, 5*time.Minute)
			tt.steps(q)
			if inner.commits != tt.writes {
				t.Errorf("writes = %d, want %d", inner.commits, tt.writes)
			}
		})
	}
}
