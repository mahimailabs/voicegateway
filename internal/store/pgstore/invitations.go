package pgstore

import (
	"context"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// CreateInvitation stores a link, revoking the open one for the same organisation, email and kind
// in the same transaction. Creates for one organisation take its row lock first, so parallel
// creates queue rather than race, and exactly one open link remains. Should an insert still meet
// the open-link index, the transaction retries once. An unknown organisation is
// store.ErrNotFound; an idempotency key used before is store.ErrIdempotencyConflict.
func (s *Store) CreateInvitation(ctx context.Context, in store.NewInvitation) (store.Invitation, error) {
	for attempt := 0; ; attempt++ {
		inv, err := s.createInvitation(ctx, in)
		var pgErr *pgconn.PgError
		switch {
		case errors.As(err, &pgErr) && pgErr.Code == uniqueViolation && pgErr.ConstraintName == "invitations_idempotency":
			return store.Invitation{}, store.ErrIdempotencyConflict
		case errors.As(err, &pgErr) && pgErr.Code == uniqueViolation && pgErr.ConstraintName == "invitations_open" && attempt == 0:
			continue
		case errors.As(err, &pgErr) && pgErr.Code == foreignKeyViolation:
			return store.Invitation{}, store.ErrNotFound
		case errors.Is(err, store.ErrNotFound):
			return store.Invitation{}, err
		case err != nil:
			return store.Invitation{}, fmt.Errorf("create invitation: %w", err)
		}
		return inv, nil
	}
}

func (s *Store) createInvitation(ctx context.Context, in store.NewInvitation) (store.Invitation, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return store.Invitation{}, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	if _, err := q.LockOrganisation(ctx, in.OrganisationID); errors.Is(err, pgx.ErrNoRows) {
		return store.Invitation{}, store.ErrNotFound
	} else if err != nil {
		return store.Invitation{}, err
	}
	// Locks come before any invitation UPDATE, always in this order: the organisation, then for a
	// reset the member's row. A reset replaces the member's open links in every organisation, which
	// the organisation lock alone does not cover; locking the member after touching invitation rows
	// would let two resets each hold what the other waits for.
	var resetUser pgtype.UUID
	if in.Kind == "reset" && in.UserID != nil {
		if resetUser, err = uuid(*in.UserID); err != nil {
			return store.Invitation{}, err
		}
		if _, err := q.LockUser(ctx, resetUser); errors.Is(err, pgx.ErrNoRows) {
			return store.Invitation{}, store.ErrNotFound
		} else if err != nil {
			return store.Invitation{}, err
		}
	}
	email := foldEmail(in.Email)
	if err := q.RevokeOpenInvitation(ctx, RevokeOpenInvitationParams{OrganisationID: in.OrganisationID, Email: email, Kind: in.Kind}); err != nil {
		return store.Invitation{}, err
	}
	if resetUser.Valid {
		if err := q.RevokeOpenResets(ctx, resetUser); err != nil {
			return store.Invitation{}, err
		}
	}
	params := InsertInvitationParams{
		Kind: in.Kind, OrganisationID: in.OrganisationID, Email: email, TokenSha256: in.TokenSHA256,
		Role: textOf(in.Role), IdempotencyKey: textOf(in.IdempotencyKey), ExpiresAt: timestamp(&in.ExpiresAt),
	}
	if params.UserID, err = uuidOf(in.UserID); err != nil {
		return store.Invitation{}, err
	}
	if params.InvitedBy, err = uuidOf(in.InvitedBy); err != nil {
		return store.Invitation{}, err
	}
	row, err := q.InsertInvitation(ctx, params)
	if err != nil {
		return store.Invitation{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return store.Invitation{}, err
	}
	return store.Invitation{ID: row.ID.String(), Kind: in.Kind, Email: email, Role: in.Role, CreatedAt: row.CreatedAt.Time.UTC(), ExpiresAt: in.ExpiresAt.UTC()}, nil
}

// InvitationByIdempotencyKey returns the invitation an idempotency key made, or store.ErrNotFound.
func (s *Store) InvitationByIdempotencyKey(ctx context.Context, organisationID, key string) (store.Invitation, error) {
	r, err := s.q.InvitationByIdempotencyKey(ctx, InvitationByIdempotencyKeyParams{OrganisationID: organisationID, IdempotencyKey: pgtype.Text{String: key, Valid: true}})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.Invitation{}, store.ErrNotFound
	}
	if err != nil {
		return store.Invitation{}, fmt.Errorf("invitation by key: %w", err)
	}
	return invitationOf(r.ID, r.Kind, r.Email, r.Role, r.InvitedByEmail, r.CreatedAt, r.ExpiresAt, r.EmailedAt, r.AcceptedAt, r.RevokedAt), nil
}

// MarkInvitationEmailed records that a link's email was sent.
func (s *Store) MarkInvitationEmailed(ctx context.Context, id string) error {
	u, err := uuid(id)
	if err != nil {
		return err
	}
	if err := s.q.MarkInvitationEmailed(ctx, u); err != nil {
		return fmt.Errorf("mark invitation emailed: %w", err)
	}
	return nil
}

// Invitations lists an organisation's invitations and reset links, newest first.
func (s *Store) Invitations(ctx context.Context, organisationID string) ([]store.Invitation, error) {
	rows, err := s.q.ListInvitations(ctx, organisationID)
	if err != nil {
		return nil, fmt.Errorf("invitations: %w", err)
	}
	out := make([]store.Invitation, 0, len(rows))
	for _, r := range rows {
		out = append(out, invitationOf(r.ID, r.Kind, r.Email, r.Role, r.InvitedByEmail, r.CreatedAt, r.ExpiresAt, r.EmailedAt, r.AcceptedAt, r.RevokedAt))
	}
	return out, nil
}

// RevokeInvitation revokes one of an organisation's links; revoking twice is fine. An id that is
// not this organisation's is store.ErrNotFound.
func (s *Store) RevokeInvitation(ctx context.Context, organisationID, id string) error {
	u, err := uuid(id)
	if err != nil {
		return store.ErrNotFound
	}
	n, err := s.q.RevokeInvitation(ctx, RevokeInvitationParams{OrganisationID: organisationID, ID: u})
	if err != nil {
		return fmt.Errorf("revoke invitation: %w", err)
	}
	if n == 0 {
		ok, err := s.q.InvitationExists(ctx, InvitationExistsParams{OrganisationID: organisationID, ID: u})
		if err != nil {
			return fmt.Errorf("revoke invitation: %w", err)
		}
		if !ok {
			return store.ErrNotFound
		}
	}
	return nil
}

// OpenInvitation reads an open, unexpired link by its token's hash, or store.ErrNotFound.
func (s *Store) OpenInvitation(ctx context.Context, tokenSHA256 []byte) (store.OpenInvitation, error) {
	r, err := s.q.OpenInvitationByToken(ctx, tokenSHA256)
	if errors.Is(err, pgx.ErrNoRows) {
		return store.OpenInvitation{}, store.ErrNotFound
	}
	if err != nil {
		return store.OpenInvitation{}, fmt.Errorf("open invitation: %w", err)
	}
	return store.OpenInvitation{
		ID: r.ID.String(), Kind: r.Kind, OrganisationID: r.OrganisationID, OrganisationName: r.OrganisationName, Email: r.Email,
		Role: strOf(r.Role), UserID: uuidStr(r.UserID),
	}, nil
}

// OrganisationName returns an organisation's name, or store.ErrNotFound.
func (s *Store) OrganisationName(ctx context.Context, id string) (string, error) {
	name, err := s.q.OrganisationName(ctx, id)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", store.ErrNotFound
	}
	if err != nil {
		return "", fmt.Errorf("organisation name: %w", err)
	}
	return name, nil
}

// OrganisationRoles returns every organisation a user belongs to, with their role there.
func (s *Store) OrganisationRoles(ctx context.Context, userID string) (map[string]string, error) {
	u, err := uuid(userID)
	if err != nil {
		return map[string]string{}, nil
	}
	rows, err := s.q.OrganisationRoles(ctx, u)
	if err != nil {
		return nil, fmt.Errorf("organisation roles: %w", err)
	}
	out := make(map[string]string, len(rows))
	for _, r := range rows {
		out[r.OrganisationID] = r.Role
	}
	return out, nil
}

// IsMemberByEmail reports whether an email's account belongs to an organisation.
func (s *Store) IsMemberByEmail(ctx context.Context, organisationID, email string) (bool, error) {
	ok, err := s.q.IsMemberByEmail(ctx, IsMemberByEmailParams{OrganisationID: organisationID, Email: foldEmail(email)})
	if err != nil {
		return false, fmt.Errorf("is member: %w", err)
	}
	return ok, nil
}

// Member returns one member of an organisation, or store.ErrNotFound.
func (s *Store) Member(ctx context.Context, organisationID, userID string) (store.Member, error) {
	u, err := uuid(userID)
	if err != nil {
		return store.Member{}, store.ErrNotFound
	}
	m, err := s.q.MemberOf(ctx, MemberOfParams{OrganisationID: organisationID, UserID: u})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.Member{}, store.ErrNotFound
	}
	if err != nil {
		return store.Member{}, fmt.Errorf("member: %w", err)
	}
	return store.Member{UserID: m.UserID.String(), Email: m.Email, Role: m.Role}, nil
}

// AcceptInvitation uses a link once and applies it, in one transaction: one conditional UPDATE ...
// RETURNING uses the link, and anything but one row is store.ErrNotFound. For an invite it adds
// existingUserID to the organisation, keeping a member's role, or, with existingUserID empty,
// creates the account with passwordHash; when that email's account was created meanwhile it
// rolls back and returns store.ErrUserExists, for the caller to verify that account's password.
// For a reset it sets passwordHash for the link's user and moves password_version. It returns the
// user's id. Hashing and verifying are the caller's, before this.
func (s *Store) AcceptInvitation(ctx context.Context, tokenSHA256 []byte, passwordHash, existingUserID string) (string, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return "", fmt.Errorf("accept invitation: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	// A reset locks its member before it touches the invitation row, the order createInvitation
	// takes (organisation, member, invitation rows), so accepting an old reset while an admin makes
	// a new one cannot deadlock. UseInvitation stays conditional: a link revoked or used meanwhile
	// still reads as not found.
	if open, err := q.OpenInvitationByToken(ctx, tokenSHA256); err == nil && open.Kind == "reset" && open.UserID.Valid {
		if _, err := q.LockUser(ctx, open.UserID); err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return "", fmt.Errorf("accept reset: %w", err)
		}
	} else if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return "", fmt.Errorf("accept invitation: %w", err)
	}
	link, err := q.UseInvitation(ctx, tokenSHA256)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", store.ErrNotFound
	}
	if err != nil {
		return "", fmt.Errorf("accept invitation: %w", err)
	}
	var userID pgtype.UUID
	switch link.Kind {
	case "reset":
		userID = link.UserID
		// The guard ResetLink ran when the link was made runs again now, failing closed: the member
		// must still belong to the link's organisation, and its admin (or, for a link made with no
		// admin, the link's organisation alone) must cover every organisation they belong to. The
		// member may have joined or left one since, or the admin lost the role. A link that no
		// longer passes is revoked and changes nothing else.
		ok, err := resetAllowed(ctx, q, link.OrganisationID, link.InvitedBy, userID)
		if err != nil {
			return "", fmt.Errorf("accept reset: %w", err)
		}
		if !ok {
			if err := q.RevokeUsedLink(ctx, link.ID); err != nil {
				return "", fmt.Errorf("accept reset: %w", err)
			}
			if err := tx.Commit(ctx); err != nil {
				return "", fmt.Errorf("accept reset: %w", err)
			}
			return "", store.ErrNotFound
		}
		if err := q.SetPasswordByID(ctx, SetPasswordByIDParams{ID: userID, PasswordHash: passwordHash}); err != nil {
			return "", fmt.Errorf("accept reset: %w", err)
		}
	default:
		if existingUserID != "" {
			if userID, err = uuid(existingUserID); err != nil {
				return "", err
			}
		} else {
			userID, err = q.CreateUserIfAbsent(ctx, CreateUserIfAbsentParams{Email: link.Email, PasswordHash: passwordHash})
			if errors.Is(err, pgx.ErrNoRows) {
				return "", ErrUserExists
			}
			if err != nil {
				return "", fmt.Errorf("accept invitation: %w", err)
			}
		}
		if err := q.AddMembershipIfAbsent(ctx, AddMembershipIfAbsentParams{UserID: userID, OrganisationID: link.OrganisationID, Role: link.Role.String}); err != nil {
			return "", fmt.Errorf("accept invitation: %w", err)
		}
	}
	if err := tx.Commit(ctx); err != nil {
		return "", fmt.Errorf("accept invitation: %w", err)
	}
	return userID.String(), nil
}

// resetAllowed is the reset guard at use: user still belongs to organisationID, and by is an
// admin of every organisation user belongs to. With no by (the command line, the instance API,
// or an admin since deleted), user must belong to organisationID alone.
func resetAllowed(ctx context.Context, q *Queries, organisationID string, by, user pgtype.UUID) (bool, error) {
	theirs, err := q.OrganisationRoles(ctx, user)
	if err != nil {
		return false, err
	}
	admin := map[string]bool{}
	if by.Valid {
		mine, err := q.OrganisationRoles(ctx, by)
		if err != nil {
			return false, err
		}
		for _, r := range mine {
			admin[r.OrganisationID] = r.Role == "admin"
		}
	}
	member := false
	for _, r := range theirs {
		member = member || r.OrganisationID == organisationID
		if by.Valid && !admin[r.OrganisationID] || !by.Valid && r.OrganisationID != organisationID {
			return false, nil
		}
	}
	return member, nil
}

func invitationOf(id pgtype.UUID, kind, email string, role, invitedBy pgtype.Text, created, expires, emailed, accepted, revoked pgtype.Timestamptz) store.Invitation {
	return store.Invitation{
		ID: id.String(), Kind: kind, Email: email, Role: strOf(role), InvitedBy: strOf(invitedBy),
		CreatedAt: created.Time.UTC(), ExpiresAt: expires.Time.UTC(),
		EmailedAt: timeOf(emailed), AcceptedAt: timeOf(accepted), RevokedAt: timeOf(revoked),
	}
}

func textOf(s *string) pgtype.Text {
	if s == nil {
		return pgtype.Text{}
	}
	return pgtype.Text{String: *s, Valid: true}
}

func strOf(t pgtype.Text) *string {
	if !t.Valid {
		return nil
	}
	s := t.String
	return &s
}

func uuidOf(s *string) (pgtype.UUID, error) {
	if s == nil {
		return pgtype.UUID{}, nil
	}
	return uuid(*s)
}

func uuidStr(u pgtype.UUID) *string {
	if !u.Valid {
		return nil
	}
	s := u.String()
	return &s
}
