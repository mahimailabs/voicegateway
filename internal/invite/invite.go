package invite

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"fmt"
	"log/slog"
	"net/mail"
	"strings"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Lifetimes (spec 06d): an invite lasts 7 days (decision 0027), a reset 24 hours.
const (
	InviteLifetime = 7 * 24 * time.Hour
	ResetLifetime  = 24 * time.Hour
	// MinPassword is the shortest password a link sets.
	MinPassword = 12
)

// The service's errors are the store's own, so the API and sign-in map them without importing
// this package.
var (
	// ErrInvalid is a bad email, role or base URL, a short password, or a reused idempotency key.
	ErrInvalid = store.ErrInvalidInvitation
	// ErrAlreadyMember means the email's account already belongs to the organisation.
	ErrAlreadyMember = store.ErrMemberExists
	// ErrInvalidLink is the one answer for an unknown, accepted, revoked or expired link.
	ErrInvalidLink = store.ErrInvalidLink
	// ErrOtherOrganisations means a reset's member belongs to an organisation the requester does
	// not administer.
	ErrOtherOrganisations = store.ErrOtherOrganisations
	// ErrWrongPassword means an existing account's password did not match.
	ErrWrongPassword = store.ErrWrongPassword
)

// TooManyAttempts is sign-in's limit, reached through accept: try again after Wait.
type TooManyAttempts = store.TooManyAttempts

// Store is what the service reads and writes.
type Store interface {
	CreateInvitation(ctx context.Context, in store.NewInvitation) (store.Invitation, error)
	InvitationByIdempotencyKey(ctx context.Context, organisationID, key string) (store.Invitation, error)
	MarkInvitationEmailed(ctx context.Context, id string) error
	OpenInvitation(ctx context.Context, tokenSHA256 []byte) (store.OpenInvitation, error)
	AcceptInvitation(ctx context.Context, tokenSHA256 []byte, passwordHash, existingUserID string) (string, error)
	OrganisationName(ctx context.Context, id string) (string, error)
	OrganisationRoles(ctx context.Context, userID string) (map[string]string, error)
	IsMemberByEmail(ctx context.Context, organisationID, email string) (bool, error)
	Member(ctx context.Context, organisationID, userID string) (store.Member, error)
	UserByEmail(ctx context.Context, email string) (store.User, error)
	Principal(ctx context.Context, userID string) (store.Principal, error)
}

// message is one plain-text email.
type message struct {
	to, subject, body string
}

// Mailer sends one plain-text email. An error may name its class with a Class() string method
// (unreachable, auth, rejected); it is never logged as text.
type Mailer interface {
	Send(ctx context.Context, to, subject, body string) error
}

// SignInLimit counts one password try for email under sign-in's own limits, keyed exactly as
// POST /auth/sign-in keys them. It returns wait > 0 when the limit is reached, and otherwise a
// success func that forgets the tries once the password proved right.
type SignInLimit = func(ctx context.Context, email string) (success func(context.Context) error, wait time.Duration, err error)

// Service makes and uses links. Mailer is nil without SMTP; Hash and Verify are sign-in's
// argon2id functions, injected since this package does not depend on sign-in.
type Service struct {
	Store  Store
	Mailer Mailer
	Hash   func(ctx context.Context, password string) (string, error)
	Verify func(ctx context.Context, password, hash string) (bool, error)
	Log    *slog.Logger
	Now    func() time.Time
}

// Invite is one invitation to make.
type Invite struct {
	OrganisationID string
	Email          string
	Role           string  // "admin", "member" or "viewer"
	InvitedBy      *string // a user id, nil for the instance API and the command line
	BaseURL        string  // the link's base, from VOICEGATEWAY_PUBLIC_URL or the request; required
	IdempotencyKey string  // optional; the 06e instance API sets it so its retries are safe
}

// Created is a made link. Link is set only without a Mailer.
type Created struct {
	Invitation store.Invitation
	Link       string // <BaseURL>/invite#<token> only without a Mailer; empty with one, and on an idempotent repeat
	Emailed    bool   // true when the send succeeded; false without SMTP, or when it failed
}

// Pending is what the accept screen shows of a link: its kind ("invite" or "reset"), the
// organisation's name, the role (nil for a reset) and the email.
type Pending = store.PendingLink

var roles = map[string]bool{"admin": true, "member": true, "viewer": true}

// Create makes an invitation for in.Email to join in.OrganisationID as in.Role, replacing any
// open invitation for that email there. With a Mailer set, it emails the link and never returns
// it, whether the send succeeded or failed: only the address's owner may hold it, or anyone who
// can invite could claim an account for an email they do not control. Without a Mailer, it
// returns the link, the only time the full link exists. in.InvitedBy is nil for the instance API
// and the command line.
//
// With in.IdempotencyKey set, a repeat call with the same key returns the invitation the first
// call made, with Emailed as it was and Link empty (only the token's hash is kept), and revokes
// nothing, so a retry never kills a link already emailed. The same key with another email or
// role is ErrInvalid.
//
// Errors: ErrInvalid (email, role, empty BaseURL, or a reused key), ErrAlreadyMember, and
// store.ErrNotFound for an unknown organisation.
func (s *Service) Create(ctx context.Context, in Invite) (Created, error) {
	email, ok := normalEmail(in.Email)
	if !ok || !roles[in.Role] || in.BaseURL == "" {
		return Created{}, ErrInvalid
	}
	if in.IdempotencyKey != "" {
		if c, done, err := s.repeat(ctx, in, email); done || err != nil {
			return c, err
		}
	}
	member, err := s.Store.IsMemberByEmail(ctx, in.OrganisationID, email)
	if err != nil {
		return Created{}, err
	}
	if member {
		return Created{}, ErrAlreadyMember
	}
	token, sum, err := newToken()
	if err != nil {
		return Created{}, err
	}
	role := in.Role
	n := store.NewInvitation{
		Kind: "invite", OrganisationID: in.OrganisationID, Email: email, Role: &role, InvitedBy: in.InvitedBy,
		TokenSHA256: sum, ExpiresAt: s.now().Add(InviteLifetime),
	}
	if in.IdempotencyKey != "" {
		key := in.IdempotencyKey
		n.IdempotencyKey = &key
	}
	inv, err := s.Store.CreateInvitation(ctx, n)
	if errors.Is(err, store.ErrIdempotencyConflict) {
		// A parallel call with the same key stored first: answer as its repeat.
		if c, done, err := s.repeat(ctx, in, email); done || err != nil {
			return c, err
		}
		return Created{}, ErrInvalid
	}
	if err != nil {
		return Created{}, err
	}
	org, err := s.Store.OrganisationName(ctx, in.OrganisationID)
	if err != nil {
		return Created{}, err
	}
	link := linkOf(in.BaseURL, token)
	body := fmt.Sprintf("You are invited to join %s on VoiceGateway as %s.\n\nOpen this link to accept:\n%s\n\nIt expires on %s.\n",
		org, role, link, inv.ExpiresAt.UTC().Format("2 January 2006 at 15:04 UTC"))
	return s.deliver(ctx, inv, message{to: email, subject: "Join " + org + " on VoiceGateway", body: body}, link)
}

// repeat answers a call whose idempotency key made an invitation already. done is false when the
// key is new.
func (s *Service) repeat(ctx context.Context, in Invite, email string) (Created, bool, error) {
	inv, err := s.Store.InvitationByIdempotencyKey(ctx, in.OrganisationID, in.IdempotencyKey)
	if errors.Is(err, store.ErrNotFound) {
		return Created{}, false, nil
	}
	if err != nil {
		return Created{}, true, err
	}
	if inv.Kind != "invite" || inv.Email != email || inv.Role == nil || *inv.Role != in.Role {
		return Created{}, true, ErrInvalid
	}
	return Created{Invitation: inv, Emailed: inv.EmailedAt != nil}, true, nil
}

// ResetLink makes a password reset link for userID, replacing any open one for them. Like Create,
// with a Mailer set it emails the link to the member and returns no link. It is refused unless
// the requester is an admin of every organisation userID belongs to, so an admin of one
// organisation cannot take over an account that also belongs to another. Errors:
// store.ErrNotFound when userID is not a member of organisationID, and ErrOtherOrganisations
// when they also belong to an organisation the requester does not administer. by is nil for the
// command line and the 06e instance API; the guard then fails closed and allows a member of
// organisationID alone. Accepting runs the guard again, and also requires the member to still
// belong to organisationID.
func (s *Service) ResetLink(ctx context.Context, organisationID, userID string, by *string, baseURL string) (Created, error) {
	if baseURL == "" {
		return Created{}, ErrInvalid
	}
	m, err := s.Store.Member(ctx, organisationID, userID)
	if err != nil {
		return Created{}, err
	}
	theirs, err := s.Store.OrganisationRoles(ctx, userID)
	if err != nil {
		return Created{}, err
	}
	mine := map[string]string{}
	if by != nil {
		if mine, err = s.Store.OrganisationRoles(ctx, *by); err != nil {
			return Created{}, err
		}
	}
	for org := range theirs {
		// With no requester, the rule is explicit rather than skipped: the member may belong to
		// this organisation only.
		if by != nil && mine[org] != "admin" || by == nil && org != organisationID {
			return Created{}, ErrOtherOrganisations
		}
	}
	token, sum, err := newToken()
	if err != nil {
		return Created{}, err
	}
	uid := m.UserID
	inv, err := s.Store.CreateInvitation(ctx, store.NewInvitation{
		Kind: "reset", OrganisationID: organisationID, Email: m.Email, UserID: &uid, InvitedBy: by,
		TokenSHA256: sum, ExpiresAt: s.now().Add(ResetLifetime),
	})
	if err != nil {
		return Created{}, err
	}
	link := linkOf(baseURL, token)
	body := fmt.Sprintf("An admin made a password reset link for your VoiceGateway account.\n\nOpen this link to set a new password:\n%s\n\nIt expires on %s. Setting a password signs you out everywhere else.\n",
		link, inv.ExpiresAt.UTC().Format("2 January 2006 at 15:04 UTC"))
	return s.deliver(ctx, inv, message{to: m.Email, subject: "Reset your VoiceGateway password", body: body}, link)
}

// deliver emails a new link when a Mailer is set, and otherwise returns it. A failed send logs its
// class and the invitation id only; the invitation stands.
func (s *Service) deliver(ctx context.Context, inv store.Invitation, m message, link string) (Created, error) {
	if s.Mailer == nil {
		return Created{Invitation: inv, Link: link}, nil
	}
	if err := s.Mailer.Send(ctx, m.to, m.subject, m.body); err != nil {
		s.Log.Warn("invite: could not send the email", "invitation_id", inv.ID, "class", classOf(err))
		return Created{Invitation: inv}, nil
	}
	if err := s.Store.MarkInvitationEmailed(ctx, inv.ID); err != nil {
		s.Log.Warn("invite: could not record the sent email", "invitation_id", inv.ID, "err", err)
	} else {
		at := s.now()
		inv.EmailedAt = &at
	}
	return Created{Invitation: inv, Emailed: true}, nil
}

// Look reads a link's invitation by its token for the accept screen: its kind, organisation
// name, role and email, never whether an account exists. ErrInvalidLink is the same for
// unknown, accepted, revoked and expired.
func (s *Service) Look(ctx context.Context, token string) (Pending, error) {
	sum, ok := tokenSum(token)
	if !ok {
		return Pending{}, ErrInvalidLink
	}
	o, err := s.Store.OpenInvitation(ctx, sum)
	if errors.Is(err, store.ErrNotFound) {
		return Pending{}, ErrInvalidLink
	}
	if err != nil {
		return Pending{}, err
	}
	return Pending{Kind: o.Kind, OrganisationName: o.OrganisationName, Role: o.Role, Email: o.Email}, nil
}

// Accept uses a link once. For an invite, password either creates the account (a new email,
// at least 12 characters) or must be the existing account's own password, checked under the
// sign-in limits; an existing member keeps their role. For a reset, it sets the password for
// the link's user_id and moves password_version, signing them out everywhere else. Hashing and
// verifying run before the transaction opens; inside it, one conditional UPDATE ... RETURNING
// uses the link, and anything but one row is ErrInvalidLink. It returns the user to sign in.
//
// Errors: ErrInvalid for a password under 12 characters, ErrInvalidLink, ErrWrongPassword, and
// TooManyAttempts.
func (s *Service) Accept(ctx context.Context, token, password string, limit SignInLimit) (store.Principal, error) {
	if len(password) < MinPassword {
		return store.Principal{}, ErrInvalid
	}
	sum, ok := tokenSum(token)
	if !ok {
		return store.Principal{}, ErrInvalidLink
	}
	link, err := s.Store.OpenInvitation(ctx, sum)
	if errors.Is(err, store.ErrNotFound) {
		return store.Principal{}, ErrInvalidLink
	}
	if err != nil {
		return store.Principal{}, err
	}
	if link.Kind == "reset" {
		hash, err := s.Hash(ctx, password)
		if err != nil {
			return store.Principal{}, err
		}
		return s.use(ctx, link, sum, hash, "")
	}
	// Every invite accept counts as a sign-in try for its email, whether or not an account
	// exists, so neither the limit nor the time taken says which.
	success, wait, err := limit(ctx, link.Email)
	if err != nil {
		return store.Principal{}, err
	}
	if wait > 0 {
		return store.Principal{}, TooManyAttempts{Wait: wait}
	}
	p, err := s.acceptInvite(ctx, link, sum, password, true)
	switch {
	case errors.Is(err, ErrInvalidLink):
		// The link was used meanwhile, before any password was tested: the try is not a failure.
		s.forget(ctx, success, link.ID)
		return store.Principal{}, err
	case err != nil:
		return store.Principal{}, err
	}
	s.forget(ctx, success, link.ID)
	return p, nil
}

// forget clears the reserved tries. The link is already decided, so a failure only logs.
func (s *Service) forget(ctx context.Context, success func(context.Context) error, id string) {
	if err := success(ctx); err != nil {
		s.Log.Warn("invite: could not clear sign-in tries", "invitation_id", id, "err", err)
	}
}

// acceptInvite creates the account or proves the existing one's password, then uses the link.
// When the account was created meanwhile, by a parallel accept, it tries once more as that
// account.
func (s *Service) acceptInvite(ctx context.Context, link store.OpenInvitation, sum []byte, password string, retry bool) (store.Principal, error) {
	u, err := s.Store.UserByEmail(ctx, link.Email)
	switch {
	case err == nil:
		ok, err := s.Verify(ctx, password, u.PasswordHash)
		if err != nil {
			return store.Principal{}, err
		}
		if !ok {
			return store.Principal{}, ErrWrongPassword
		}
		return s.use(ctx, link, sum, "", u.ID)
	case !errors.Is(err, store.ErrNotFound):
		return store.Principal{}, err
	}
	hash, err := s.Hash(ctx, password)
	if err != nil {
		return store.Principal{}, err
	}
	p, err := s.use(ctx, link, sum, hash, "")
	if errors.Is(err, store.ErrUserExists) && retry {
		// A parallel accept created the account first. Its password, not this one, is the
		// account's; a mismatch reads as the link being used, as the spec allows.
		p, err := s.acceptInvite(ctx, link, sum, password, false)
		if errors.Is(err, ErrWrongPassword) {
			return store.Principal{}, ErrInvalidLink
		}
		return p, err
	}
	if errors.Is(err, store.ErrUserExists) {
		return store.Principal{}, ErrInvalidLink
	}
	return p, err
}

// use applies the link in one transaction and reads the user to sign in.
func (s *Service) use(ctx context.Context, link store.OpenInvitation, sum []byte, hash, existingUserID string) (store.Principal, error) {
	userID, err := s.Store.AcceptInvitation(ctx, sum, hash, existingUserID)
	if errors.Is(err, store.ErrNotFound) {
		s.Log.Info("invite: a link was used up meanwhile", "invitation_id", link.ID)
		return store.Principal{}, ErrInvalidLink
	}
	if err != nil {
		return store.Principal{}, err
	}
	return s.Store.Principal(ctx, userID)
}

func (s *Service) now() time.Time {
	if s.Now != nil {
		return s.Now()
	}
	return time.Now()
}

// tokenLen is the length of a token: 32 bytes, base64url without padding.
const tokenLen = 43

// newToken returns a fresh token and its SHA-256.
func newToken() (string, []byte, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", nil, fmt.Errorf("invitation token: %w", err)
	}
	t := base64.RawURLEncoding.EncodeToString(b)
	sum := sha256.Sum256([]byte(t))
	return t, sum[:], nil
}

// tokenSum returns a presented token's SHA-256, or false when it cannot be one of ours.
func tokenSum(t string) ([]byte, bool) {
	if len(t) != tokenLen {
		return nil, false
	}
	if _, err := base64.RawURLEncoding.DecodeString(t); err != nil {
		return nil, false
	}
	sum := sha256.Sum256([]byte(t))
	return sum[:], true
}

// TokenSum is tokenSum for tests and callers that look a link up by hand.
func TokenSum(t string) []byte {
	sum := sha256.Sum256([]byte(t))
	return sum[:]
}

func linkOf(base, token string) string {
	return strings.TrimRight(base, "/") + "/invite#" + token
}

// normalEmail returns the case-folded bare address, or false when s is not one.
func normalEmail(s string) (string, bool) {
	a, err := mail.ParseAddress(s)
	if err != nil || a.Name != "" || a.Address != strings.TrimSpace(s) || len(a.Address) > 254 {
		return "", false
	}
	return strings.ToLower(a.Address), true
}

// classOf names a send failure's class, never its text, which may quote the server.
func classOf(err error) string {
	var c interface{ Class() string }
	if errors.As(err, &c) {
		return c.Class()
	}
	return "unknown"
}
