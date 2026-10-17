package pgstore

import (
	"context"
	"errors"
	"fmt"
	"math"
	"slices"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// ErrUserExists means an account with that email already exists.
var ErrUserExists = errors.New("a user with that email exists")

// CountUsers returns how many accounts exist.
func (s *Store) CountUsers(ctx context.Context) (int64, error) {
	n, err := s.q.CountUsers(ctx)
	if err != nil {
		return 0, fmt.Errorf("count users: %w", err)
	}
	return n, nil
}

// AddUser creates an account with a role in one organisation, in one transaction. The email is
// case-folded. An email already in use is ErrUserExists, and nothing is written.
func (s *Store) AddUser(ctx context.Context, email, passwordHash, organisationID, role string) (string, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return "", fmt.Errorf("add user: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	ok, err := q.OrganisationExists(ctx, organisationID)
	if err != nil {
		return "", fmt.Errorf("add user: %w", err)
	}
	if !ok {
		return "", fmt.Errorf("organisation %s: %w", organisationID, store.ErrNotFound)
	}
	id, err := q.CreateUser(ctx, CreateUserParams{Email: foldEmail(email), PasswordHash: passwordHash})
	if err != nil {
		var pgErr *pgconn.PgError
		if errors.As(err, &pgErr) && pgErr.Code == uniqueViolation {
			return "", ErrUserExists
		}
		return "", fmt.Errorf("add user: %w", err)
	}
	if err := q.AddMembership(ctx, AddMembershipParams{UserID: id, OrganisationID: organisationID, Role: role}); err != nil {
		return "", fmt.Errorf("add membership: %w", err)
	}
	if err := tx.Commit(ctx); err != nil {
		return "", fmt.Errorf("add user: %w", err)
	}
	return id.String(), nil
}

// UserByEmail finds an account by its email, or store.ErrNotFound.
func (s *Store) UserByEmail(ctx context.Context, email string) (store.User, error) {
	row, err := s.q.UserByEmail(ctx, foldEmail(email))
	if errors.Is(err, pgx.ErrNoRows) {
		return store.User{}, store.ErrNotFound
	}
	if err != nil {
		return store.User{}, fmt.Errorf("user by email: %w", err)
	}
	return store.User{ID: row.ID.String(), Email: row.Email, PasswordHash: row.PasswordHash}, nil
}

// Principal loads a user's email and organisation roles, or store.ErrNotFound.
func (s *Store) Principal(ctx context.Context, userID string) (store.Principal, error) {
	id, err := uuid(userID)
	if err != nil {
		return store.Principal{}, store.ErrNotFound
	}
	u, err := s.q.UserByID(ctx, id)
	if errors.Is(err, pgx.ErrNoRows) {
		return store.Principal{}, store.ErrNotFound
	}
	if err != nil {
		return store.Principal{}, fmt.Errorf("user: %w", err)
	}
	rows, err := s.q.Memberships(ctx, id)
	if err != nil {
		return store.Principal{}, fmt.Errorf("memberships: %w", err)
	}
	p := store.Principal{UserID: u.ID.String(), Email: u.Email, Roles: map[string]string{}}
	for _, r := range rows {
		p.Roles[r.OrganisationID] = r.Role
	}
	return p, nil
}

// ReserveSignIn reserves one sign-in attempt under every key, atomically: in one transaction,
// each key's advisory lock is taken in a fixed order, its attempts since a time are counted, and
// when any key is at its limit nothing is recorded and the call reports when the attempt that keeps
// it there leaves the window (retryAfter is the latest such time). Otherwise one attempt is
// recorded per key and their ids returned, for a successful sign-in to forget.
func (s *Store) ReserveSignIn(ctx context.Context, keys []store.SignInKey, since time.Time) (ids []int64, limitedSince time.Time, err error) {
	keys = slices.Clone(keys)
	for i := range keys {
		if keys[i].Kind == "email" {
			keys[i].Key = foldEmail(keys[i].Key)
		}
	}
	slices.SortFunc(keys, func(a, b store.SignInKey) int { return strings.Compare(a.Kind+"/"+a.Key, b.Kind+"/"+b.Key) })
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return nil, time.Time{}, fmt.Errorf("reserve sign-in: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	for _, k := range keys {
		if err := q.LockSignInKey(ctx, LockSignInKeyParams{LockKind: k.Kind, LockKey: k.Key}); err != nil {
			return nil, time.Time{}, fmt.Errorf("reserve sign-in: %w", err)
		}
	}
	for _, k := range keys {
		n, err := q.SignInAttemptsSince(ctx, SignInAttemptsSinceParams{Kind: k.Kind, Key: k.Key, Since: timestamp(&since)})
		if err != nil {
			return nil, time.Time{}, fmt.Errorf("reserve sign-in: %w", err)
		}
		if n < int64(k.Max) {
			continue
		}
		at, err := q.LimitingSignInAttempt(ctx, LimitingSignInAttemptParams{Kind: k.Kind, Key: k.Key, Since: timestamp(&since), Skip: int32(min(k.Max-1, math.MaxInt32))}) //nolint:gosec // bounded above
		if err != nil {
			return nil, time.Time{}, fmt.Errorf("reserve sign-in: %w", err)
		}
		if at.Time.After(limitedSince) {
			limitedSince = at.Time
		}
	}
	if !limitedSince.IsZero() {
		return nil, limitedSince, nil
	}
	for _, k := range keys {
		id, err := q.RecordSignInAttempt(ctx, RecordSignInAttemptParams{Kind: k.Kind, Key: k.Key})
		if err != nil {
			return nil, time.Time{}, fmt.Errorf("reserve sign-in: %w", err)
		}
		ids = append(ids, id)
	}
	if err := tx.Commit(ctx); err != nil {
		return nil, time.Time{}, fmt.Errorf("reserve sign-in: %w", err)
	}
	return ids, time.Time{}, nil
}

// ForgetSignInAttempts deletes the attempts a successful sign-in reserved.
func (s *Store) ForgetSignInAttempts(ctx context.Context, ids []int64) error {
	if err := s.q.ForgetSignInAttempts(ctx, ids); err != nil {
		return fmt.Errorf("forget sign-in attempts: %w", err)
	}
	return nil
}

// FailedSignIns returns how many sign-ins failed for one key since a time, and the oldest of them.
func (s *Store) FailedSignIns(ctx context.Context, kind, key string, since time.Time) (int64, time.Time, error) {
	if kind == "email" {
		key = foldEmail(key)
	}
	row, err := s.q.FailedSignIns(ctx, FailedSignInsParams{Kind: kind, Key: key, Since: timestamp(&since)})
	if err != nil {
		return 0, time.Time{}, fmt.Errorf("sign-in failures: %w", err)
	}
	return row.Failures, row.Oldest.Time, nil
}

// ForgetSignIns deletes failures older than a time.
func (s *Store) ForgetSignIns(ctx context.Context, before time.Time) error {
	if err := s.q.ForgetSignIns(ctx, timestamp(&before)); err != nil {
		return fmt.Errorf("forget sign-in failures: %w", err)
	}
	return nil
}

// Workspaces returns the organisations a user belongs to with their projects; empty when none.
func (s *Store) Workspaces(ctx context.Context, userID string) ([]store.Workspace, error) {
	id, err := uuid(userID)
	if err != nil {
		return []store.Workspace{}, nil
	}
	rows, err := s.q.WorkspaceProjects(ctx, id)
	if err != nil {
		return nil, fmt.Errorf("workspaces: %w", err)
	}
	out := []store.Workspace{}
	for _, r := range rows {
		if len(out) == 0 || out[len(out)-1].ID != r.OrganisationID {
			out = append(out, store.Workspace{ID: r.OrganisationID, Name: r.OrganisationName, Projects: []store.WorkspaceProject{}})
		}
		if r.ProjectID.Valid {
			w := &out[len(out)-1]
			w.Projects = append(w.Projects, store.WorkspaceProject{ID: r.ProjectID.String, Name: r.ProjectName.String})
		}
	}
	return out, nil
}

func foldEmail(email string) string { return strings.ToLower(strings.TrimSpace(email)) }
