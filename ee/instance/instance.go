// Package instance is the instance API (phase 06e, decision 0028): an operator's program, holding
// the instance token, creates organisations and invites their first admin over HTTP. It is
// Enterprise code under ee/LICENSE; internal/app wires it only when VOICEGATEWAY_INSTANCE_TOKEN is
// set, and internal/api registers its handlers behind the one access gate.
package instance

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"regexp"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/api"
	"github.com/mahimailabs/voicegateway/internal/invite"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// maxBody bounds a request body, which holds a name, or an email and a role.
const maxBody = 1 << 10

// maxUsageSpan is the longest period getOrganisationUsage reads.
const maxUsageSpan = 62 * 24 * time.Hour

// maxName is the longest organisation name taken, in characters.
const maxName = 200

// orgPattern is the organisations table's id check (migrations/postgres/00001_projects.sql).
var orgPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,62}$`)

// Store creates, renames and reads organisations.
type Store interface {
	PutOrganisation(ctx context.Context, id, name string) (store.Organisation, bool, error)
	Organisation(ctx context.Context, id string) (store.Organisation, error)
	OrganisationUsage(ctx context.Context, id string, from, to time.Time) (store.Usage, error)
}

// Inviter makes invitations: 06d's invite.Service.
type Inviter interface {
	Create(ctx context.Context, in invite.Invite) (invite.Created, error)
}

// Handlers serve the instance API's four operations.
type Handlers struct {
	Store   Store
	Invites Inviter
	// BaseURL is VOICEGATEWAY_PUBLIC_URL, every invitation link's base; never a request's Host.
	BaseURL string
	Log     *slog.Logger
}

// API returns the handlers as internal/api registers them.
func (h *Handlers) API() api.InstanceHandlers {
	return api.InstanceHandlers{
		PutOrganisation:              h.putOrganisation,
		GetOrganisation:              h.getOrganisation,
		CreateOrganisationInvitation: h.createOrganisationInvitation,
		GetOrganisationUsage:         h.getOrganisationUsage,
	}
}

// organisation is the contract's InstanceOrganisation.
type organisation struct {
	ID                 string    `json:"id"`
	Name               string    `json:"name"`
	CreatedAt          time.Time `json:"createdAt"`
	Members            int64     `json:"members"`
	Admins             int64     `json:"admins"`
	PendingInvitations int64     `json:"pendingInvitations"`
}

// createdInvitation is the contract's InstanceInvitation. Link is null whenever SMTP is set, and on
// a repeat.
type createdInvitation struct {
	InvitationID string    `json:"invitationId"`
	Email        string    `json:"email"`
	Role         string    `json:"role"`
	ExpiresAt    time.Time `json:"expiresAt"`
	Emailed      *bool     `json:"emailed"` // null on a repeat whose first send's outcome was lost
	Link         *string   `json:"link"`
}

// usage is the contract's InstanceUsage.
type usage struct {
	OrganisationID      string    `json:"organisationId"`
	From                time.Time `json:"from"`
	To                  time.Time `json:"to"`
	CallSeconds         int64     `json:"callSeconds"`
	Calls               int64     `json:"calls"`
	InferredCalls       int64     `json:"inferredCalls"`
	UnendedCalls        int64     `json:"unendedCalls"`
	UnmeasuredCalls     int64     `json:"unmeasuredCalls"`
	InferredCallSeconds int64     `json:"inferredCallSeconds"`
}

func toOrganisation(o store.Organisation) organisation {
	return organisation{
		ID: o.ID, Name: o.Name, CreatedAt: o.CreatedAt,
		Members: o.Members, Admins: o.Admins, PendingInvitations: o.PendingInvitations,
	}
}

// orgID returns the path's organisation id, or answers 400 when it breaks the id pattern.
func orgID(w http.ResponseWriter, r *http.Request) (string, bool) {
	id := chi.URLParam(r, "orgId")
	if !orgPattern.MatchString(id) {
		api.WriteError(w, r, http.StatusBadRequest, api.CodeInvalidRequest, "orgId must match ^[a-z0-9][a-z0-9-]{0,62}$")
		return "", false
	}
	return id, true
}

// decode reads a bounded JSON body with no unknown fields.
func decode(r *http.Request, v any) error {
	dec := json.NewDecoder(io.LimitReader(r.Body, maxBody))
	dec.DisallowUnknownFields()
	return dec.Decode(v)
}

// done logs one call's operation, organisation and outcome: never the token, an invitation token,
// a link or an email.
func (h *Handlers) done(r *http.Request, op, org, outcome string) {
	h.Log.Info("instance api", "operation", op, "organisation", org, "outcome", outcome)
}

// fail answers 500 and logs the error, which carries no credential.
func (h *Handlers) fail(w http.ResponseWriter, r *http.Request, op, org string, err error) {
	h.Log.Error("instance api", "operation", op, "organisation", org, "outcome", "error", "err", err)
	api.WriteError(w, r, http.StatusInternalServerError, api.CodeInternal, "the request could not be completed")
}

// putOrganisation serves operationId putOrganisation: 201 when this call created the organisation,
// 200 when it existed, renamed to the name sent.
func (h *Handlers) putOrganisation(w http.ResponseWriter, r *http.Request) {
	const op = "putOrganisation"
	id, ok := orgID(w, r)
	if !ok {
		h.done(r, op, "", "invalid")
		return
	}
	var body struct {
		Name string `json:"name"`
	}
	if err := decode(r, &body); err != nil || strings.TrimSpace(body.Name) == "" || len([]rune(body.Name)) > maxName {
		h.done(r, op, id, "invalid")
		api.WriteError(w, r, http.StatusBadRequest, api.CodeInvalidRequest, "name is required, at most 200 characters")
		return
	}
	if _, created, err := h.Store.PutOrganisation(r.Context(), id, strings.TrimSpace(body.Name)); err != nil {
		h.fail(w, r, op, id, err)
		return
	} else if created {
		h.answerOrganisation(w, r, op, id, http.StatusCreated, "created")
		return
	}
	h.answerOrganisation(w, r, op, id, http.StatusOK, "exists")
}

// getOrganisation serves operationId getOrganisation.
func (h *Handlers) getOrganisation(w http.ResponseWriter, r *http.Request) {
	const op = "getOrganisation"
	id, ok := orgID(w, r)
	if !ok {
		h.done(r, op, "", "invalid")
		return
	}
	h.answerOrganisation(w, r, op, id, http.StatusOK, "read")
}

// answerOrganisation reads the organisation with its counts and answers it with status.
func (h *Handlers) answerOrganisation(w http.ResponseWriter, r *http.Request, op, id string, status int, outcome string) {
	o, err := h.Store.Organisation(r.Context(), id)
	if errors.Is(err, store.ErrNotFound) {
		h.done(r, op, id, "not_found")
		api.WriteError(w, r, http.StatusNotFound, api.CodeNotFound, "no such organisation")
		return
	}
	if err != nil {
		h.fail(w, r, op, id, err)
		return
	}
	h.done(r, op, id, outcome)
	api.WriteJSON(w, status, toOrganisation(o))
}

// idempotencyKey returns the request's Idempotency-Key: 1 to 200 printable ASCII characters.
func idempotencyKey(r *http.Request) (string, bool) {
	k := r.Header.Get("Idempotency-Key")
	if k == "" || len(k) > 200 {
		return "", false
	}
	for i := 0; i < len(k); i++ {
		if k[i] < 0x20 || k[i] > 0x7e {
			return "", false
		}
	}
	return k, true
}

// storedRole maps the contract's role to the stored one, or "" when it is not one.
func storedRole(role string) string {
	switch role {
	case "Admin", "Member", "Viewer":
		return strings.ToLower(role)
	}
	return ""
}

// contractRole maps a stored role back to the contract's name.
func contractRole(stored string) string {
	if stored == "" {
		return stored
	}
	return strings.ToUpper(stored[:1]) + stored[1:]
}

// createOrganisationInvitation serves operationId createOrganisationInvitation: 06d's invitation,
// with no inviting user and the request's Idempotency-Key. 201 when this call made it; 200 with
// the first invitation, link null and nothing sent, when the key made it before.
func (h *Handlers) createOrganisationInvitation(w http.ResponseWriter, r *http.Request) {
	const op = "createOrganisationInvitation"
	id, ok := orgID(w, r)
	if !ok {
		h.done(r, op, "", "invalid")
		return
	}
	key, ok := idempotencyKey(r)
	if !ok {
		h.done(r, op, id, "invalid")
		api.WriteError(w, r, http.StatusBadRequest, api.CodeInvalidRequest, "an Idempotency-Key header of 1 to 200 printable characters is required")
		return
	}
	var body struct {
		Email string `json:"email"`
		Role  string `json:"role"`
	}
	if err := decode(r, &body); err != nil || body.Email == "" || storedRole(body.Role) == "" {
		h.done(r, op, id, "invalid")
		api.WriteError(w, r, http.StatusBadRequest, api.CodeInvalidRequest, "email and a role of Admin, Member or Viewer are required")
		return
	}
	c, err := h.Invites.Create(r.Context(), invite.Invite{
		OrganisationID: id, Email: body.Email, Role: storedRole(body.Role), InvitedBy: nil,
		BaseURL: h.BaseURL, IdempotencyKey: key,
	})
	switch {
	case errors.Is(err, invite.ErrInvalid):
		h.done(r, op, id, "invalid")
		api.WriteError(w, r, http.StatusBadRequest, api.CodeInvalidRequest, "email must be one address and role Admin, Member or Viewer; a used Idempotency-Key must repeat its email and role")
		return
	case errors.Is(err, invite.ErrAlreadyMember):
		h.done(r, op, id, "already_member")
		api.WriteError(w, r, http.StatusConflict, api.CodeAlreadyMember, "that email's account is already a member of this organisation")
		return
	case errors.Is(err, store.ErrNotFound):
		h.done(r, op, id, "not_found")
		api.WriteError(w, r, http.StatusNotFound, api.CodeNotFound, "no such organisation")
		return
	case err != nil:
		h.fail(w, r, op, id, err)
		return
	}
	out := createdInvitation{InvitationID: c.Invitation.ID, Email: c.Invitation.Email, ExpiresAt: c.Invitation.ExpiresAt}
	if !c.EmailUnknown {
		out.Emailed = &c.Emailed
	}
	if c.Invitation.Role != nil {
		out.Role = contractRole(*c.Invitation.Role)
	}
	if c.Link != "" {
		out.Link = &c.Link
	}
	status, outcome := http.StatusCreated, "created"
	if c.Repeat {
		status, outcome = http.StatusOK, "repeat"
	}
	switch {
	case c.EmailUnknown:
		outcome += ", email outcome unknown"
	case !c.Emailed && out.Link == nil:
		outcome += ", not emailed"
	}
	h.done(r, op, id, outcome)
	api.WriteJSON(w, status, out)
}

// getOrganisationUsage serves operationId getOrganisationUsage: the organisation's call time in
// [from, to), at most 62 days. Turning seconds into billed minutes is the caller's job.
func (h *Handlers) getOrganisationUsage(w http.ResponseWriter, r *http.Request) {
	const op = "getOrganisationUsage"
	id, ok := orgID(w, r)
	if !ok {
		h.done(r, op, "", "invalid")
		return
	}
	q := r.URL.Query()
	from, errFrom := time.Parse(time.RFC3339, q.Get("from"))
	to, errTo := time.Parse(time.RFC3339, q.Get("to"))
	if errFrom != nil || errTo != nil || !to.After(from) || to.Sub(from) > maxUsageSpan {
		h.done(r, op, id, "invalid")
		api.WriteError(w, r, http.StatusBadRequest, api.CodeInvalidRequest, "from and to must be RFC 3339 instants, to after from, at most 62 days apart")
		return
	}
	u, err := h.Store.OrganisationUsage(r.Context(), id, from, to)
	if errors.Is(err, store.ErrNotFound) {
		h.done(r, op, id, "not_found")
		api.WriteError(w, r, http.StatusNotFound, api.CodeNotFound, "no such organisation")
		return
	}
	if err != nil {
		h.fail(w, r, op, id, err)
		return
	}
	h.done(r, op, id, "read")
	api.WriteJSON(w, http.StatusOK, usage{
		OrganisationID: id, From: from.UTC(), To: to.UTC(),
		CallSeconds: u.CallSeconds, Calls: u.Calls, InferredCalls: u.InferredCalls, UnendedCalls: u.UnendedCalls, UnmeasuredCalls: u.UnmeasuredCalls, InferredCallSeconds: u.InferredCallSeconds,
	})
}
