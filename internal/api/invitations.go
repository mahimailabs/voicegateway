package api

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// maxInvitationBody bounds a createInvitation body, which holds an email and a role.
const maxInvitationBody = 1 << 10

// Contract Error codes for invitations.
const (
	codeAlreadyMember       = "already_member"
	codeOtherOrganisations  = "member_of_other_organisations"
	invitationsUnreadable   = "the invitations could not be read"
	invitationsNotWritten   = "the invitation could not be made"
	passwordResetNotWritten = "the password reset link could not be made"
	invitationElsewhere     = "no such invitation in this organisation"
)

// Invitations lists and revokes an organisation's invitations and reset links.
type Invitations interface {
	Invitations(ctx context.Context, organisationID string) ([]store.Invitation, error)
	RevokeInvitation(ctx context.Context, organisationID, id string) error
}

// Inviter makes invitations and reset links: the invite service, wired in internal/app. link is
// empty when the link went by email. Its errors are store.ErrInvalidInvitation,
// store.ErrMemberExists, store.ErrOtherOrganisations and store.ErrNotFound.
type Inviter interface {
	Invite(ctx context.Context, organisationID, email, role string, by *string, baseURL string) (inv store.Invitation, link string, emailed bool, err error)
	ResetLink(ctx context.Context, organisationID, userID string, by *string, baseURL string) (inv store.Invitation, link string, emailed bool, err error)
}

// invitation is the contract's Invitation: never a token or a link.
type invitation struct {
	ID        string    `json:"id"`
	Kind      string    `json:"kind"`
	Email     string    `json:"email"`
	Role      *string   `json:"role"`
	State     string    `json:"state"`
	CreatedAt time.Time `json:"createdAt"`
	ExpiresAt time.Time `json:"expiresAt"`
	InvitedBy *string   `json:"invitedBy"`
}

type invitations struct {
	Items []invitation `json:"items"`
}

// createdInvitation is the contract's CreatedInvitation; Link is null whenever SMTP is set.
type createdInvitation struct {
	Invitation invitation `json:"invitation"`
	Link       *string    `json:"link"`
	Emailed    bool       `json:"emailed"`
}

// createdPasswordReset is the contract's CreatedPasswordReset.
type createdPasswordReset struct {
	Link      *string   `json:"link"`
	ExpiresAt time.Time `json:"expiresAt"`
	Emailed   bool      `json:"emailed"`
}

func toInvitation(i store.Invitation, now time.Time) invitation {
	var role *string
	if i.Role != nil {
		r := contractRole(*i.Role)
		role = &r
	}
	return invitation{
		ID: i.ID, Kind: i.Kind, Email: i.Email, Role: role, State: i.State(now),
		CreatedAt: i.CreatedAt, ExpiresAt: i.ExpiresAt, InvitedBy: i.InvitedBy,
	}
}

// linkOrNull is a link the answer may carry: null when it went by email.
func linkOrNull(link string) *string {
	if link == "" {
		return nil
	}
	return &link
}

// requester is the signed-in admin making a link, nil for none.
func requester(r *http.Request) (*string, *string) {
	p, _ := store.PrincipalFrom(r.Context())
	if p.UserID == "" {
		return nil, nil
	}
	id, email := p.UserID, p.Email
	return &id, &email
}

// getInvitations serves operationId getInvitations: the organisation's invitations and reset
// links, newest first, each with its state at this read.
func (a *API) getInvitations(w http.ResponseWriter, r *http.Request) {
	list, err := a.Invitations.Invitations(r.Context(), chi.URLParam(r, "orgId"))
	if err != nil {
		a.failWith(w, r, err, "", invitationsUnreadable)
		return
	}
	now := a.Now()
	out := invitations{Items: make([]invitation, 0, len(list))}
	for _, i := range list {
		out.Items = append(out.Items, toInvitation(i, now))
	}
	writeJSON(w, http.StatusOK, out)
}

// createInvitation serves operationId createInvitation. The link is in the answer only without
// SMTP; with SMTP it went by email, whether or not the send succeeded.
func (a *API) createInvitation(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Email string `json:"email"`
		Role  string `json:"role"`
	}
	dec := json.NewDecoder(io.LimitReader(r.Body, maxInvitationBody))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&body); err != nil || storedRole(body.Role) == "" || body.Email == "" {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "email and a role of Admin, Member or Viewer are required")
		return
	}
	by, byEmail := requester(r)
	inv, link, emailed, err := a.Inviter.Invite(r.Context(), chi.URLParam(r, "orgId"), body.Email, storedRole(body.Role), by, a.LinkBase(r))
	switch {
	case errors.Is(err, store.ErrInvalidInvitation):
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "email must be one address, and role Admin, Member or Viewer")
		return
	case errors.Is(err, store.ErrMemberExists):
		writeError(w, r, http.StatusConflict, codeAlreadyMember, "that email's account is already a member of this organisation")
		return
	case err != nil:
		a.failWith(w, r, err, "organisation not found", invitationsNotWritten)
		return
	}
	inv.InvitedBy = byEmail
	writeJSON(w, http.StatusCreated, createdInvitation{Invitation: toInvitation(inv, a.Now()), Link: linkOrNull(link), Emailed: emailed})
}

// revokeInvitation serves operationId revokeInvitation: 204 every time, for this organisation's
// links.
func (a *API) revokeInvitation(w http.ResponseWriter, r *http.Request) {
	if err := a.Invitations.RevokeInvitation(r.Context(), chi.URLParam(r, "orgId"), chi.URLParam(r, "invitationId")); err != nil {
		a.failWith(w, r, err, invitationElsewhere, "the invitation could not be revoked")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// createPasswordReset serves operationId createPasswordReset, under the same rule as invitations:
// with SMTP the link goes to the member only.
func (a *API) createPasswordReset(w http.ResponseWriter, r *http.Request) {
	by, _ := requester(r)
	inv, link, emailed, err := a.Inviter.ResetLink(r.Context(), chi.URLParam(r, "orgId"), chi.URLParam(r, "userId"), by, a.LinkBase(r))
	switch {
	case errors.Is(err, store.ErrOtherOrganisations):
		writeError(w, r, http.StatusConflict, codeOtherOrganisations, "this member also belongs to an organisation you do not administer; an admin of every one of them can reset their password")
		return
	case err != nil:
		a.failWith(w, r, err, "no such member of this organisation", passwordResetNotWritten)
		return
	}
	writeJSON(w, http.StatusCreated, createdPasswordReset{Link: linkOrNull(link), ExpiresAt: inv.ExpiresAt, Emailed: emailed})
}
