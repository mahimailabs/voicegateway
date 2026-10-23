package pgstore

import (
	"context"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// UserByIdentity returns the account a provider subject is linked to (decision 0035), or
// store.ErrNotFound.
func (s *Store) UserByIdentity(ctx context.Context, provider, subject string) (store.User, error) {
	row, err := s.q.UserByIdentity(ctx, UserByIdentityParams{Provider: provider, Subject: subject})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.User{}, store.ErrNotFound
	}
	if err != nil {
		return store.User{}, fmt.Errorf("user by identity: %w", err)
	}
	return store.User{ID: row.ID.String(), Email: row.Email, PasswordHash: row.PasswordHash.String, PasswordVersion: int(row.PasswordVersion)}, nil
}

// LinkIdentity links a provider subject to userID, with the verified email it was linked by.
// Linking it again to the same account does nothing; a subject linked to another account is
// store.ErrIdentityLinked, and keeps that account.
func (s *Store) LinkIdentity(ctx context.Context, provider, subject, userID, email string) error {
	id, err := uuid(userID)
	if err != nil {
		return store.ErrNotFound
	}
	if err := s.q.InsertIdentity(ctx, InsertIdentityParams{Provider: provider, Subject: subject, UserID: id, Email: foldEmail(email)}); err != nil {
		return fmt.Errorf("link identity: %w", err)
	}
	owner, err := s.q.IdentityOwner(ctx, IdentityOwnerParams{Provider: provider, Subject: subject})
	if err != nil {
		// No row: the account was deleted between the two statements, and its link with it.
		if errors.Is(err, pgx.ErrNoRows) {
			return store.ErrNotFound
		}
		return fmt.Errorf("link identity: %w", err)
	}
	if owner != id {
		return store.ErrIdentityLinked
	}
	return nil
}

// TouchIdentity records a sign-in through a linked provider subject.
func (s *Store) TouchIdentity(ctx context.Context, provider, subject string) error {
	if err := s.q.TouchIdentity(ctx, TouchIdentityParams{Provider: provider, Subject: subject}); err != nil {
		return fmt.Errorf("touch identity: %w", err)
	}
	return nil
}

// SignInMethods reports whether an account has a password, and lists its linked providers, in one
// query; store.ErrNotFound for an unknown account.
func (s *Store) SignInMethods(ctx context.Context, userID string) (bool, []store.Identity, error) {
	id, err := uuid(userID)
	if err != nil {
		return false, nil, store.ErrNotFound
	}
	rows, err := s.q.SignInMethods(ctx, id)
	if err != nil {
		return false, nil, fmt.Errorf("sign-in methods: %w", err)
	}
	if len(rows) == 0 {
		return false, nil, store.ErrNotFound
	}
	out := make([]store.Identity, 0, len(rows))
	for _, r := range rows {
		if r.Provider.Valid {
			out = append(out, store.Identity{Provider: r.Provider.String, LinkedAt: r.CreatedAt.Time})
		}
	}
	return rows[0].HasPassword, out, nil
}
