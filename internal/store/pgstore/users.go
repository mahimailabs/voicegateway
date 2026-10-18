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
	"github.com/jackc/pgx/v5/pgtype"

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
	return store.User{ID: row.ID.String(), Email: row.Email, PasswordHash: row.PasswordHash, PasswordVersion: int(row.PasswordVersion)}, nil
}

// Principal loads a user's email, password version and organisation roles in one query, or
// store.ErrNotFound.
func (s *Store) Principal(ctx context.Context, userID string) (store.Principal, error) {
	id, err := uuid(userID)
	if err != nil {
		return store.Principal{}, store.ErrNotFound
	}
	// One query for the user and every role: the gate runs it on every request, so it is one round
	// trip, never cached, and a role change or removal applies on the next request on every replica.
	rows, err := s.q.PrincipalRows(ctx, id)
	if err != nil {
		return store.Principal{}, fmt.Errorf("principal: %w", err)
	}
	if len(rows) == 0 {
		return store.Principal{}, store.ErrNotFound
	}
	u := rows[0]
	p := store.Principal{UserID: u.ID.String(), Email: u.Email, Roles: map[string]string{}, PasswordVersion: int(u.PasswordVersion)}
	for _, r := range rows {
		if r.OrganisationID.Valid {
			p.Roles[r.OrganisationID.String] = r.Role.String
		}
	}
	return p, nil
}

// ReserveSignIn reserves one sign-in attempt under every key, atomically: in one transaction,
// each key's advisory lock is taken in a fixed order and its attempts in the 15-minute window are
// counted, by the Postgres clock. When any key is at its limit nothing is recorded, and wait says
// how long until the attempt that keeps it there leaves the window (the longest over the keys).
// Otherwise one attempt is recorded per key and their ids returned, for a successful sign-in to
// forget.
func (s *Store) ReserveSignIn(ctx context.Context, keys []store.SignInKey) (ids []int64, wait time.Duration, err error) {
	keys = slices.Clone(keys)
	for i := range keys {
		if keys[i].Kind == "email" {
			keys[i].Key = foldEmail(keys[i].Key)
		}
	}
	slices.SortFunc(keys, func(a, b store.SignInKey) int { return strings.Compare(a.Kind+"/"+a.Key, b.Kind+"/"+b.Key) })
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return nil, 0, fmt.Errorf("reserve sign-in: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	for _, k := range keys {
		if err := q.LockSignInKey(ctx, LockSignInKeyParams{LockKind: k.Kind, LockKey: k.Key}); err != nil {
			return nil, 0, fmt.Errorf("reserve sign-in: %w", err)
		}
	}
	for _, k := range keys {
		n, err := q.SignInAttemptsSince(ctx, SignInAttemptsSinceParams{Kind: k.Kind, Key: k.Key})
		if err != nil {
			return nil, 0, fmt.Errorf("reserve sign-in: %w", err)
		}
		if n < int64(k.Max) {
			continue
		}
		secs, err := q.LimitingSignInAttempt(ctx, LimitingSignInAttemptParams{Kind: k.Kind, Key: k.Key, Skip: int32(min(k.Max-1, math.MaxInt32))}) //nolint:gosec // bounded above
		if err != nil {
			return nil, 0, fmt.Errorf("reserve sign-in: %w", err)
		}
		// At least a second: the attempt can leave the window between the count and this read.
		wait = max(wait, time.Duration(secs*float64(time.Second)), time.Second)
	}
	if wait > 0 {
		return nil, wait, nil
	}
	for _, k := range keys {
		id, err := q.RecordSignInAttempt(ctx, RecordSignInAttemptParams{Kind: k.Kind, Key: k.Key})
		if err != nil {
			return nil, 0, fmt.Errorf("reserve sign-in: %w", err)
		}
		ids = append(ids, id)
	}
	if err := tx.Commit(ctx); err != nil {
		return nil, 0, fmt.Errorf("reserve sign-in: %w", err)
	}
	return ids, 0, nil
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

// ForgetSignIns deletes attempts past the 15-minute window, by the Postgres clock.
func (s *Store) ForgetSignIns(ctx context.Context) error {
	if err := s.q.ForgetSignIns(ctx); err != nil {
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

// AddMembership gives an existing account a role in another organisation. An unknown email or
// organisation is store.ErrNotFound; a user already in it is store.ErrMemberExists.
func (s *Store) AddMembership(ctx context.Context, email, organisationID, role string) (string, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return "", fmt.Errorf("add membership: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	ok, err := q.OrganisationExists(ctx, organisationID)
	if err != nil {
		return "", fmt.Errorf("add membership: %w", err)
	}
	if !ok {
		return "", fmt.Errorf("organisation %s: %w", organisationID, store.ErrNotFound)
	}
	u, err := q.UserByEmail(ctx, foldEmail(email))
	if errors.Is(err, pgx.ErrNoRows) {
		return "", fmt.Errorf("user %s: %w", foldEmail(email), store.ErrNotFound)
	}
	if err != nil {
		return "", fmt.Errorf("add membership: %w", err)
	}
	if err := q.AddMembership(ctx, AddMembershipParams{UserID: u.ID, OrganisationID: organisationID, Role: role}); err != nil {
		var pgErr *pgconn.PgError
		if errors.As(err, &pgErr) && pgErr.Code == uniqueViolation {
			return "", store.ErrMemberExists
		}
		return "", fmt.Errorf("add membership: %w", err)
	}
	if err := tx.Commit(ctx); err != nil {
		return "", fmt.Errorf("add membership: %w", err)
	}
	return u.ID.String(), nil
}

// UpdatePasswordHash stores the same password hashed again with current parameters, only while the
// stored hash is still verifiedHash, the one the sign-in checked. It is not a new password, so
// password_version stays and no session is signed out. If the hash changed meanwhile, a password
// reset landed, and it reports false and changes nothing.
func (s *Store) UpdatePasswordHash(ctx context.Context, userID, verifiedHash, passwordHash string) (bool, error) {
	id, err := uuid(userID)
	if err != nil {
		return false, fmt.Errorf("update password hash: %w", err)
	}
	n, err := s.q.UpdatePasswordHash(ctx, UpdatePasswordHashParams{ID: id, PasswordHash: passwordHash, VerifiedHash: verifiedHash})
	if err != nil {
		return false, fmt.Errorf("update password hash: %w", err)
	}
	return n == 1, nil
}

// SetPassword replaces an account's password and moves its password_version on, which signs the
// user out of every session. An unknown email is store.ErrNotFound.
func (s *Store) SetPassword(ctx context.Context, email, passwordHash string) error {
	n, err := s.q.SetPassword(ctx, SetPasswordParams{Email: foldEmail(email), PasswordHash: passwordHash})
	if err != nil {
		return fmt.Errorf("set password: %w", err)
	}
	if n == 0 {
		return fmt.Errorf("user %s: %w", foldEmail(email), store.ErrNotFound)
	}
	return nil
}

// Members returns an organisation's members, ordered by email.
func (s *Store) Members(ctx context.Context, organisationID string) ([]store.Member, error) {
	rows, err := s.q.OrganisationMembers(ctx, organisationID)
	if err != nil {
		return nil, fmt.Errorf("members: %w", err)
	}
	out := make([]store.Member, 0, len(rows))
	for _, r := range rows {
		out = append(out, store.Member{UserID: r.UserID.String(), Email: r.Email, Role: r.Role})
	}
	return out, nil
}

// UpdateMember sets a member's role. A user who is not a member of the organisation is
// store.ErrNotFound; demoting its last admin is store.ErrLastAdmin.
func (s *Store) UpdateMember(ctx context.Context, organisationID, userID, role string) (store.Member, error) {
	var out store.Member
	err := s.changeMember(ctx, organisationID, userID, role != "admin", func(q *Queries, id pgtype.UUID) error {
		if _, err := q.SetMemberRole(ctx, SetMemberRoleParams{OrganisationID: organisationID, UserID: id, Role: role}); err != nil {
			return err
		}
		m, err := q.MemberOf(ctx, MemberOfParams{OrganisationID: organisationID, UserID: id})
		out = store.Member{UserID: m.UserID.String(), Email: m.Email, Role: m.Role}
		return err
	})
	return out, err
}

// RemoveMember removes a user from an organisation; their account and sessions stay. A user who is
// not a member is store.ErrNotFound; removing the last admin is store.ErrLastAdmin.
func (s *Store) RemoveMember(ctx context.Context, organisationID, userID string) error {
	return s.changeMember(ctx, organisationID, userID, true, func(q *Queries, id pgtype.UUID) error {
		_, err := q.RemoveMembership(ctx, RemoveMembershipParams{OrganisationID: organisationID, UserID: id})
		return err
	})
}

// changeMember runs one change to a membership in a transaction that first locks the organisation
// row, so concurrent changes are serialised, then refuses when the change takes the admin role
// from the organisation's only admin.
func (s *Store) changeMember(ctx context.Context, organisationID, userID string, losesAdmin bool, change func(*Queries, pgtype.UUID) error) error {
	id, err := uuid(userID)
	if err != nil {
		return store.ErrNotFound
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return fmt.Errorf("change member: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	if _, err := q.LockOrganisation(ctx, organisationID); errors.Is(err, pgx.ErrNoRows) {
		return store.ErrNotFound
	} else if err != nil {
		return fmt.Errorf("change member: %w", err)
	}
	m, err := q.MemberOf(ctx, MemberOfParams{OrganisationID: organisationID, UserID: id})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.ErrNotFound
	}
	if err != nil {
		return fmt.Errorf("change member: %w", err)
	}
	if losesAdmin && m.Role == "admin" {
		n, err := q.CountAdmins(ctx, organisationID)
		if err != nil {
			return fmt.Errorf("change member: %w", err)
		}
		if n <= 1 {
			return store.ErrLastAdmin
		}
	}
	if err := change(q, id); err != nil {
		return fmt.Errorf("change member: %w", err)
	}
	if err := tx.Commit(ctx); err != nil {
		return fmt.Errorf("change member: %w", err)
	}
	return nil
}

func foldEmail(email string) string { return strings.ToLower(strings.TrimSpace(email)) }
