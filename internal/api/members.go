package api

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// maxMemberBody bounds an updateMember body, which holds one role.
const maxMemberBody = 1 << 10

// codeLastAdmin is the contract's Error code for a change that would leave no admin.
const codeLastAdmin = "last_admin"

// members is the contract's Members.
type members struct {
	Items []member `json:"items"`
}

// member is the contract's Member.
type member struct {
	UserID string `json:"userId"`
	Email  string `json:"email"`
	Role   string `json:"role"`
}

func toMember(m store.Member) member {
	return member{UserID: m.UserID, Email: m.Email, Role: contractRole(m.Role)}
}

// getMembers serves operationId getMembers: the organisation's members, ordered by email.
func (a *API) getMembers(w http.ResponseWriter, r *http.Request) {
	ms, err := a.Accounts.Members(r.Context(), chi.URLParam(r, "orgId"))
	if err != nil {
		a.fail(w, r, err, "")
		return
	}
	out := members{Items: make([]member, 0, len(ms))}
	for _, m := range ms {
		out.Items = append(out.Items, toMember(m))
	}
	writeJSON(w, http.StatusOK, out)
}

// updateMember serves operationId updateMember: a new role for one member.
func (a *API) updateMember(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Role string `json:"role"`
	}
	dec := json.NewDecoder(io.LimitReader(r.Body, maxMemberBody))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&body); err != nil || storedRole(body.Role) == "" {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "role must be Admin, Member or Viewer")
		return
	}
	m, err := a.Accounts.UpdateMember(r.Context(), chi.URLParam(r, "orgId"), chi.URLParam(r, "userId"), storedRole(body.Role))
	if err != nil {
		a.memberFail(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, toMember(m))
}

// removeMember serves operationId removeMember. The user's account and sessions stay; they lose
// this organisation on their next request, since the principal is loaded on every request.
func (a *API) removeMember(w http.ResponseWriter, r *http.Request) {
	if err := a.Accounts.RemoveMember(r.Context(), chi.URLParam(r, "orgId"), chi.URLParam(r, "userId")); err != nil {
		a.memberFail(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (a *API) memberFail(w http.ResponseWriter, r *http.Request, err error) {
	if errors.Is(err, store.ErrLastAdmin) {
		writeError(w, r, http.StatusConflict, codeLastAdmin, "an organisation needs at least one admin")
		return
	}
	a.fail(w, r, err, "no such member of this organisation")
}
